document.addEventListener("DOMContentLoaded", function () {
    const map = window.map;
    if (!map) {
        console.error("Leaflet map instance not found.");
        return;
    }

    map.eachLayer(function (layer) {
        if (layer.feature && layer.feature.properties) {
            const layerType = layer.feature.properties.type;
            const highlightedId = window[layerType + "Highlighted"];
            if (highlightedId && layer.feature.properties.index === highlightedId) {
                console.log("Bringing layer to front:", layer.feature.properties.index);
                layer.bringToFront();
            }
        }
    });

    function applyLineHoverEffects(line) {
        line.on("mouseover", function () {
            line.setStyle({ color: "lime", weight: 4 });
        });

        line.on("mouseout", function () {
            line.setStyle({ color: "green", weight: 3 });
        });
    }

    let satelliteMode = false;

    function applyStyle(layerGroup, styleGroup, layerType) {
        if (!layerGroup) return;
        const highlightedId = window[layerType + "Highlighted"];

        layerGroup.eachLayer(layer => {
            if (!layer.feature || !layer.feature.properties) return;
            const id = layer.feature.properties.index;
            const isHighlight = id === highlightedId;
            const style = isHighlight ? styleGroup[layerType]["highlight"] : styleGroup[layerType]["default"];
            layer.setStyle(style);
        });
    }

    const btn = document.getElementById("layerToggleButton");
    if (btn) {
        btn.addEventListener("click", function () {
            const mode = satelliteMode ? "map" : "satellite";
            const esriSatellite = window.esriSatellite;
            const cartoPositron = window.cartoPositron;

            applyStyle(window.existing, styleMap[mode], "existing");
            applyStyle(window.new, styleMap[mode], "new");

            if (!satelliteMode) {
                if (cartoPositron && map.hasLayer(cartoPositron)) map.removeLayer(cartoPositron);
                if (esriSatellite && !map.hasLayer(esriSatellite)) map.addLayer(esriSatellite);
                this.innerText = "Switch to Map";
            } else {
                if (esriSatellite && map.hasLayer(esriSatellite)) map.removeLayer(esriSatellite);
                if (cartoPositron && !map.hasLayer(cartoPositron)) map.addLayer(cartoPositron);
                this.innerText = "Switch to Satellite";
            }

            satelliteMode = !satelliteMode;
        });
    }

    window.removedMatches = [];
    window.addedMatches = [];

    let selected = { existing: null, new: null };
    let highlightedLayer = null;

    const initialMatches = window.initialMatches;
    if (!initialMatches || !initialMatches.features || initialMatches.features.length === 0) {
        return;
    }
    const initialMatchKeys = new Set(
        initialMatches.features.map(f => `${f.properties.id_existing}--${f.properties.id_new}`)
    );
    const matchLayer = L.geoJSON(initialMatches, {
        style: function (feature) {
            return { color: 'green', weight: 3 };
        },
        onEachFeature: function (feature, layer) {
            // Allow users to remove initial matching edges
            layer.on("click", function () {
                map.removeLayer(layer);
                window.removedMatches.push({
                    id_existing: feature.properties.id_existing,
                    id_new: feature.properties.id_new
                });
                console.log("Removed match:", feature.properties.id_existing, "→", feature.properties.id_new);
            });
            applyLineHoverEffects(layer);
        }
    }).addTo(map);

    function matchExists(from_id, to_id) {
        const key = `${from_id}--${to_id}`;
        if (initialMatchKeys.has(key) &&
            !window.removedMatches.some(m => m.id_existing === from_id && m.id_new === to_id)) {
            return true;
        }
        if (window.addedMatches.some(m => m.id_existing === from_id && m.id_new === to_id)) {
            return true;
        }
        return false;
    }

    // Add new matches by clicking existing + new buildings
    map.eachLayer(layer => {
        if (layer.feature && layer.feature.properties && layer.feature.geometry) {
            const type = layer.feature.properties.type;
            const id = layer.feature.properties.index;
            const centroid = L.geoJSON(layer.feature).getBounds().getCenter();

            if (type === "existing" || type === "new") {
                layer.off("mouseover");
                layer.off("mouseout");

                layer.on("click", function () {
                    const alreadySelected = selected[type]?.id === id;

                    if (alreadySelected) {
                        selected[type] = null;
                        if (highlightedLayer && highlightedLayer._originalStyle) {
                            highlightedLayer.setStyle(highlightedLayer._originalStyle);
                            highlightedLayer = null;
                        }
                        return;
                    }

                    if (highlightedLayer && highlightedLayer._originalStyle) {
                        highlightedLayer.setStyle(highlightedLayer._originalStyle);
                    }

                    selected[type] = {
                        id: id,
                        latlng: centroid
                    };

                    layer._originalStyle = {
                        color: layer.options.color,
                        weight: layer.options.weight,
                        dashArray: layer.options.dashArray || null
                    };

                    layer.setStyle({
                        color: "gold",
                        weight: 5,
                        dashArray: "5,5"
                    });
                    highlightedLayer = layer;

                    if (selected.existing && selected.new) {
                        const from = selected.existing;
                        const to = selected.new;

                        if (matchExists(from.id, to.id)) {
                            console.log("Skipped adding match (duplicate):", from.id, "→", to.id);
                            selected = { existing: null, new: null };
                            if (highlightedLayer && highlightedLayer._originalStyle) {
                                highlightedLayer.setStyle(highlightedLayer._originalStyle);
                                highlightedLayer = null;
                            }
                            return;
                        }

                        const line = L.polyline([from.latlng, to.latlng], {
                            color: "green",
                            weight: 3
                        }).addTo(map);

                        applyLineHoverEffects(line);

                        line.on("click", function () {
                            map.removeLayer(line);
                            if (window.addedMatches.some(m => m.id_existing === from.id && m.id_new === to.id)) {
                                window.addedMatches = window.addedMatches.filter(match => match.layer !== line);
                                console.log("Removed added match:", from.id, "→", to.id);
                            } else {
                                window.removedMatches.push({
                                    id_existing: from.id,
                                    id_new: to.id
                                });
                                console.log("Removed re-added match:", from.id, "→", to.id);
                            }
                        });

                        if (window.removedMatches.some(m => m.id_existing === from.id && m.id_new === to.id)) {
                            window.removedMatches = window.removedMatches.filter(
                                m => !(m.id_existing === from.id && m.id_new === to.id)
                            );
                            console.log("Re-added removed match:", from.id, "→", to.id);
                        } else {
                            window.addedMatches.push({
                                id_existing: from.id,
                                id_new: to.id,
                                layer: line
                            });
                            console.log("Added match:", from.id, "→", to.id);
                        }

                        selected = { existing: null, new: null };
                        if (highlightedLayer && highlightedLayer._originalStyle) {
                            highlightedLayer.setStyle(highlightedLayer._originalStyle);
                            highlightedLayer = null;
                        }
                    }
                });
            }
        }
    });
});
