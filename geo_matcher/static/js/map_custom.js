
document.addEventListener('DOMContentLoaded', () => {
  const MapController = {
    map: null,
    styleMap: window.styleMap || {},
    initialMatches: window.initialMatches,
    removedMatches: [],
    addedMatches: [],
    selected: { existing: null, new: null },
    highlightedLayer: null,

    init() {
      this.map = window.map;
      if (!this.map) {
        console.error('Leaflet map instance not found.');
        return;
      }
      this.bringHighlightedLayersToFront();
      this.initMatchInteractivity();
      this.initSatelliteToggle();
    },

    bringHighlightedLayersToFront() {
      ['existing', 'new'].forEach(type => {
        const id = this.getHighlightedId(type);
        if (!id) return;
        this.map.eachLayer(layer => {
          if (layer.feature?.properties?.type === type &&
              layer.feature.properties.index === id) {
            layer.bringToFront();
          }
        });
      });
    },

    getHighlightedId(type) {
      return window[`${type}Highlighted`];
    },

    applyStyle(group, mode, type) {
      const styleGroup = this.styleMap[mode]?.[type];
      if (!group || !styleGroup) return;
      group.eachLayer(layer => {
        const id = layer.feature?.properties?.index;
        const style = (id === this.getHighlightedId(type)) ? styleGroup.highlight : styleGroup.default;
        layer.setStyle(style);
      });
    },

    initSatelliteToggle() {
      const btn = document.getElementById('satelliteToggleButton');
      if (!btn) return;
      let satelliteMode = false;

      btn.addEventListener('click', () => {
        const mode = satelliteMode ? 'map' : 'satellite';
        this.applyStyle(window.existing, mode, 'existing');
        this.applyStyle(window.new, mode, 'new');

        const { esriSatellite, cartoPositron } = window;
        if (!satelliteMode) {
          cartoPositron && this.map.removeLayer(cartoPositron);
          esriSatellite && this.map.addLayer(esriSatellite);
          btn.textContent = 'Switch to Map';
        } else {
          esriSatellite && this.map.removeLayer(esriSatellite);
          cartoPositron && this.map.addLayer(cartoPositron);
          btn.textContent = 'Switch to Satellite';
        }
        satelliteMode = !satelliteMode;
      });
    },

    initMatchInteractivity() {
      if (!this.initialMatches?.features?.length) return;
      this.cacheInitialKeys();
      this.drawInitialMatches();
      this.setupAddingNewMatches();
    },

    cacheInitialKeys() {
      this.initialKeys = new Set(
        this.initialMatches.features.map(f => `${f.properties.id_existing}--${f.properties.id_new}`)
      );
    },

    drawInitialMatches() {
      const layer = L.geoJSON(this.initialMatches, {
        style: () => ({ color: 'green', weight: 3 }),
        onEachFeature: (feat, lyr) => {
          lyr.on('click', () => this.removeMatchLayer(feat, lyr));
          this.applyHover(lyr);
        }
      }).addTo(this.map);
      this.matchLayer = layer;
    },

    removeMatchLayer(feature, layer) {
      this.map.removeLayer(layer);
      const entry = { id_existing: feature.properties.id_existing, id_new: feature.properties.id_new };
      this.removedMatches.push(entry);
      console.log(`Removed initial match: ${entry.id_existing} → ${entry.id_new}`);
    },

    applyHover(line) {
      line.on('mouseover', () => line.setStyle({ color: 'lime', weight: 4 }));
      line.on('mouseout',  () => line.setStyle({ color: 'green', weight: 3 }));
    },

    setupAddingNewMatches() {
      this.map.eachLayer(layer => {
        const props = layer.feature?.properties;
        if (!props || !['existing','new'].includes(props.type)) return;

        layer.off('mouseover');
        layer.off('mouseout');
        layer.on('click', () => this.onBuildingClick(layer, props.type, props.index));
      });
    },

    onBuildingClick(layer, type, id) {
      const center = L.geoJSON(layer.feature).getBounds().getCenter();
      if (this.selected[type]?.id === id) {
        this.clearSelection(type);
        return;
      }
      this.clearHighlight();
      this.setSelection(type, id, center, layer);
      if (this.selected.existing && this.selected.new) {
        this.handlePair();
      }
    },

    clearSelection(type) {
      this.selected[type] = null;
      this.clearHighlight();
    },

    clearHighlight() {
      if (this.highlightedLayer?._originalStyle) {
        this.highlightedLayer.setStyle(this.highlightedLayer._originalStyle);
        this.highlightedLayer = null;
      }
    },

    setSelection(type, id, latlng, layer) {
      this.selected[type] = { id, latlng };
      layer._originalStyle = { color: layer.options.color, weight: layer.options.weight, dashArray: layer.options.dashArray || null };
      layer.setStyle({ color: 'gold', weight: 5, dashArray: '5,5' });
      this.highlightedLayer = layer;
    },

    handlePair() {
      const from = this.selected.existing;
      const to   = this.selected.new;
      const key  = `${from.id}--${to.id}`;
      if (this.initialKeys.has(key) && !this.removedMatches.some(m => `${m.id_existing}--${m.id_new}` === key) ||
          this.addedMatches.some(m => `${m.id_existing}--${m.id_new}` === key)) {
        console.log(`Skipped adding duplicate match: ${from.id} → ${to.id}`);
        this.reset();
        return;
      }
      this.addMatchLine(from, to);
      this.reset();
    },

    addMatchLine(from, to) {
      const line = L.polyline([from.latlng, to.latlng], { color: 'green', weight: 3 }).addTo(this.map);
      this.applyHover(line);
      line.on('click', () => this.removeAddedLine(line, from, to));

      if (this.removedMatches.some(m => m.id_existing === from.id && m.id_new === to.id)) {
        this.removedMatches = this.removedMatches.filter(
          m => !(m.id_existing === from.id && m.id_new === to.id)
        );
        console.log(`Re-added removed match: ${from.id} → ${to.id}`);
      } else {
        this.addedMatches.push({ id_existing: from.id, id_new: to.id, layer: line });
        console.log(`Added match: ${from.id} → ${to.id}`);
      }
    },

    removeAddedLine(line, from, to) {
      this.map.removeLayer(line);
      const entry = { id_existing: from.id, id_new: to.id };
      if (this.addedMatches.some(m => `${m.id_existing}--${m.id_new}` === `${entry.id_existing}--${entry.id_new}`)) {
        this.addedMatches = this.addedMatches.filter(m => m.layer !== line);
        console.log(`Removed added match: ${entry.id_existing} → ${entry.id_new}`);
      } else {
        this.removedMatches.push(entry);
        console.log(`Removed re-added match: ${entry.id_existing} → ${entry.id_new}`);
      }
    },

    reset() {
      this.clearHighlight();
      this.selected = { existing: null, new: null };
    }
  };

  MapController.init();
});
