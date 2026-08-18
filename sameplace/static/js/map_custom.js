const TYPE_EXISTING = 'existing';
const TYPE_NEW      = 'new';
const LINE_STYLE = { color: 'green', weight: 3 };
const HOVER_STYLE = { color: 'lime', weight: 4 };

function makeMatchKey(exId, newId) {
  return `${exId}--${newId}`;
}

function bringToFront(map, type, id) {
  map.eachLayer(layer => {
    const props = layer.feature?.properties;
    if (props?.type === type && props.index === id) {
      layer.bringToFront();
    }
  });
}

function applyStyleGroup(group, styleMap, mode, type, highlightId) {
  if (!group) return;
  const styles = styleMap[mode]?.[type];
  if (!styles) return;
  group.eachLayer(layer => {
    const id = layer.feature?.properties?.index;
    const style = (id === highlightId) ? styles.highlight : styles.default;
    layer.setStyle(style);
  });
}

class MapController {
  constructor({
    map,
    styleMap,
    existingGroup,
    newGroup,
    esriSatellite,
    cartoPositron,
    satelliteToggleButton,
    initialMatches
  }) {
    this.map            = map;
    this.styleMap       = styleMap;
    this.existingGroup  = existingGroup;
    this.newGroup       = newGroup;
    this.esriSatellite  = esriSatellite;
    this.cartoPositron  = cartoPositron;
    this.btn            = satelliteToggleButton;

    // track match states: 'initial', 'removed', 'added'
    this.matchState     = new Map();
    this.initialKeys    = new Set();
    this.initialMatches = initialMatches || { features: [] };

    this.selected         = { [TYPE_EXISTING]: null, [TYPE_NEW]: null };
    this.highlightedLayer = null;
    this.originalStyles   = new WeakMap();

    this._init();
  }

  _init() {
    if (!this.map) {
      console.error('Leaflet map instance not found.');
      return;
    }
    this._initMatches();
    this._bringHighlightedLayersToFront();
    this._initSatelliteToggle();
  }

  _bringHighlightedLayersToFront() {
    [TYPE_EXISTING, TYPE_NEW].forEach(type => {
      const id = window[`${type}Highlighted`];
      if (id) bringToFront(this.map, type, id);
    });
  }

  _initSatelliteToggle() {
    if (!this.btn) return;
    let satelliteMode = false;
    this.btn.addEventListener('click', () => {
      const mode = satelliteMode ? 'map' : 'satellite';
      applyStyleGroup(this.existingGroup, this.styleMap, mode, TYPE_EXISTING, window.existingHighlighted);
      applyStyleGroup(this.newGroup,      this.styleMap, mode, TYPE_NEW,      window.newHighlighted);

      if (!satelliteMode) {
        this.map.removeLayer(this.cartoPositron);
        this.map.addLayer(this.esriSatellite);
        this.btn.textContent = 'Switch to Map';
      } else {
        this.map.removeLayer(this.esriSatellite);
        this.map.addLayer(this.cartoPositron);
        this.btn.textContent = 'Switch to Satellite';
      }
      satelliteMode = !satelliteMode;
    });
  }

  _initMatches() {
    if (!this.initialMatches?.features?.length) return;

    this.initialMatches.features.forEach(f => {
      const { id_existing, id_new } = f.properties;
      const key = makeMatchKey(id_existing, id_new);

      this.initialKeys.add(key);
      this.matchState.set(key, 'initial');
    });

    this._drawInitialMatches();
    this._setupAddingNewMatches();
  }

  _drawInitialMatches() {
    this.matchLayer = L.geoJSON(this.initialMatches, {
      style: () => LINE_STYLE,
      onEachFeature: (f, line) => {
        const { id_existing, id_new } = f.properties;
        const key = makeMatchKey(id_existing, id_new);

        line.on('click', () => this._removeMatch(line, key));
        this._applyHover(line);
      }
    }).addTo(this.map);
  }

  _removeMatch(line, key) {
    this.map.removeLayer(line);
    this.matchState.set(key, 'removed');
    console.log(`Removed match: ${key}`);
  }

  _applyHover(layer) {
    layer.on('mouseover', () => layer.setStyle(HOVER_STYLE));
    layer.on('mouseout',  () => layer.setStyle(LINE_STYLE));
  }

  _setupAddingNewMatches() {
    this.map.eachLayer(layer => {
      const props = layer.feature?.properties;
      if (!props || ![TYPE_EXISTING, TYPE_NEW].includes(props.type)) return;

      layer.off('mouseover mouseout click');
      layer.on('click', () => {
        const center = L.geoJSON(layer.feature).getBounds().getCenter();
        this._onBuildingClick(layer, props.type, props.index, center);
      });
    });
  }

  _onBuildingClick(layer, type, id, latlng) {
    if (this.selected[type]?.id === id) {
      this._clearSelection(type);
      return;
    }
    this._clearHighlight();
    this._setSelection(type, id, latlng, layer);
    if (this.selected[TYPE_EXISTING] && this.selected[TYPE_NEW]) {
      this._handlePair();
    }
  }

  _clearSelection(type) {
    this.selected[type] = null;
    this._clearHighlight();
  }

  _clearHighlight() {
    if (this.highlightedLayer) {
      const original = this.originalStyles.get(this.highlightedLayer);
      if (original) this.highlightedLayer.setStyle(original);
      this.highlightedLayer = null;
    }
  }

  _setSelection(type, id, latlng, layer) {
    this.selected[type] = { id, latlng };
    const original = { ...layer.options };
    this.originalStyles.set(layer, original);
    layer.setStyle({ color: 'gold', weight: 5, dashArray: '5,5' });
    this.highlightedLayer = layer;
  }

  _handlePair() {
    const from = this.selected[TYPE_EXISTING];
    const to   = this.selected[TYPE_NEW];
    const key  = makeMatchKey(from.id, to.id);
    const state = this.matchState.get(key);

    if (state === 'initial' || state === 'added') {
      console.log(`Skipped duplicate match: ${key}`);
    } else {
      this._addMatch(from, to, key);
    }
    this._reset();
  }

  _addMatch(from, to, key) {
    const line = L.polyline([from.latlng, to.latlng], LINE_STYLE).addTo(this.map);
    this._applyHover(line);
    line.on('click', () => this._removeMatch(line, key));

    this.matchState.set(key, 'added');
    console.log(`Added match: ${key}`);
  }

  _reset() {
    this._clearHighlight();
    this.selected = { [TYPE_EXISTING]: null, [TYPE_NEW]: null };
  }

  getRemovedMatches() {
    return Array.from(this.matchState.entries())
      .filter(([key, state]) => state === 'removed' && this.initialKeys.has(key))
      .map(([key]) => {
        const [id_existing, id_new] = key.split('--');
        return { id_existing, id_new };
      });
  }

  getAddedMatches() {
    return Array.from(this.matchState.entries())
      .filter(([key, state]) => state === 'added' && !this.initialKeys.has(key))
      .map(([key]) => {
        const [id_existing, id_new] = key.split('--');
        return { id_existing, id_new };
      });
  }
}

document.addEventListener('DOMContentLoaded', () => {
  window.mapController = new MapController({
    map: window.map,
    styleMap: window.styleMap,
    existingGroup: window.existing,
    newGroup: window.new,
    esriSatellite: window.esriSatellite,
    cartoPositron: window.cartoPositron,
    satelliteToggleButton: document.getElementById('satelliteToggleButton'),
    initialMatches: window.initialMatches,
  });
});
