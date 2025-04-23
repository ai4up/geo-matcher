from pathlib import Path
from typing import Optional

from geopandas import GeoDataFrame
import folium
import geopandas as gpd

from eubucco_conflator.state import State as S
from eubucco_conflator import spatial


def create_tutorial_html(filepath: str) -> None:
    """
    Create a demo Folium HTML map with an example candidate pair and an instruction text.
    """
    # Load demo data
    demo_data_path = Path(__file__).parent / "data" / "tutorial-candidate.parquet"
    gdf = gpd.read_parquet(demo_data_path)
    existing_buildings = gdf.loc[["A", "A_candidate"]]
    new_buildings = gdf.loc[["B", "B_candidate"]]

    c = new_buildings.centroid.loc["B_candidate"]
    lat, lon = spatial.to_lat_lon(c.x, c.y, existing_buildings.crs)

    # Initialize map and add demo buildings
    m = _initialize_map(lat, lon, 20)
    _create_existing_buildings_layer(existing_buildings, "A_candidate").add_to(m)
    _create_new_buildings_layer(new_buildings, "B_candidate").add_to(m)
    _add_tutorial_marker(lat, lon).add_to(m)
    _add_baselayer_marker().add_to(m)

    m.save(filepath)


def create_candidate_pair_html(id_existing: str, id_new: str, filepath: Path) -> None:
    """
    Create a Folium HTML map with a candidate pair.
    """
    if filepath.is_file():
        return

    candidate_pair = S.get_candidate_pair(id_existing, id_new)

    c = candidate_pair["geometry_new"].centroid
    existing_buildings = S.get_existing_buildings_at(c)
    new_buildings = S.get_new_building_at(c)

    lat, lon = spatial.to_lat_lon(c.x, c.y, existing_buildings.crs)
    m = _initialize_map(lat, lon, 20)

    _create_existing_buildings_layer(existing_buildings, id_existing).add_to(m)
    _create_new_buildings_layer(new_buildings, id_new).add_to(m)
    _disable_leaflet_click_outline(m)
    _add_legend(m, candidates_highlighted=True)

    folium.LayerControl(collapsed=True).add_to(m)

    m.save(filepath)


def create_neighborhood_html(id: str, filepath: Path) -> None:
    """
    Create a Folium HTML map with all candidate pairs in a neighborhood.
    """
    if filepath.is_file():
        return

    candidate_pairs = S.get_candidate_pairs(id)
    existing_buildings = S.get_existing_buildings(id)
    new_buildings = S.get_new_buildings(id)

    new_buildings = new_buildings.loc[candidate_pairs["id_new"].unique()]
    existing_buildings = existing_buildings.loc[candidate_pairs["id_existing"].unique()]

    lat, lon = spatial.center_lat_lon(candidate_pairs["geometry_new"])
    m = _initialize_map(lat, lon, 19)

    _create_existing_buildings_layer(existing_buildings).add_to(m)
    _create_new_buildings_layer(new_buildings).add_to(m)
    _add_matching_layer(m, candidate_pairs)
    _disable_leaflet_click_outline(m)
    _add_legend(m)

    folium.LayerControl(collapsed=True).add_to(m)

    m.save(filepath)


def _initialize_map(lat: float, lon: float, zoom_level: int) -> folium.Map:
    m = folium.Map(location=[lat, lon], zoom_start=zoom_level, tiles=None)
    folium.TileLayer("CartoDB.Positron", name="CartoDB Positron", show=True).add_to(m)
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap", show=False).add_to(m)
    folium.TileLayer(
        "Esri.WorldTopoMap",
        name="Esri WorldTopoMap",
        show=False,
        max_native_zoom=18,
        max_zoom=19,
    ).add_to(m)

    return m


def _add_tutorial_marker(lat: float, lon: float) -> folium.Marker:
    return folium.Marker(
        location=[lat, lon],
        tooltip="Building pair to be labeled as 'Match' / 'No Match' / 'Unsure'.",
        icon=folium.Icon(color="lightred", icon="info-sign"),
    )


def _add_baselayer_marker() -> folium.Marker:
    return folium.Marker(
        location=[44.80484, 3.34594],
        tooltip="Building from the baselayer. Can be ignored. Choose 'Esri WorldTopoMap' for a baselayer without buildings (lower resolution).",
        icon=folium.Icon(color="lightgray", icon="info-sign"),
    )


def _create_existing_buildings_layer(gdf: GeoDataFrame, highlight_id: Optional[str] = None) -> folium.FeatureGroup:
    def style_function(feature):
        if highlight_id and feature["properties"].get("index") == highlight_id:
            return {"color": "steelblue", "fillOpacity": 0.5, "fillColor": "skyblue", "weight": 5}
        else:
            return {"color": "skyblue", "fillOpacity": 0.5}

    def highlight_function(_):
            return {"fillOpacity": 0.8}

    gdf["type"] = "existing"
    existing_buildings = folium.FeatureGroup(name="Existing Buildings")
    if not gdf.empty:
        tooltip = folium.GeoJsonTooltip(fields=["index"], aliases=["Building ID"])
        folium.GeoJson(
            gdf.reset_index(),
            tooltip=tooltip,
            style_function=style_function,
            highlight_function=highlight_function if highlight_id else None,
        ).add_to(existing_buildings)

    return existing_buildings


def _create_new_buildings_layer(gdf: GeoDataFrame, highlight_id: Optional[str] = None) -> folium.FeatureGroup:
    def style_function(feature):
        if highlight_id and feature["properties"].get("index") == highlight_id:
            return {"color": "orangered", "fillOpacity": 0.2, "fillColor": "coral", "weight": 5}
        else:
            return {"color": "coral", "fillOpacity": 0.2}

    def highlight_function(_):
            return {"fillOpacity": 0.5}

    gdf["type"] = "new"
    new_buildings = folium.FeatureGroup(name="New Buildings")
    tooltip = folium.GeoJsonTooltip(fields=["index"], aliases=["Building ID"])
    folium.GeoJson(
        gdf.reset_index(),
        tooltip=tooltip,
        style_function=style_function,
        highlight_function=highlight_function if highlight_id else None,
    ).add_to(new_buildings)

    return new_buildings


def _add_matching_layer(m: folium.Map, candidate_pairs: GeoDataFrame) -> None:
    matches = candidate_pairs[candidate_pairs["match"]]
    if matches.empty:
        matching_edges = gpd.GeoDataFrame(geometry=[], crs=candidate_pairs["geometry_existing"].crs)
    else:
        matching_edges = spatial.connect_with_lines(
            matches.set_index("id_existing")["geometry_existing"],
            matches.set_index("id_new")["geometry_new"]
        ).reset_index(names=["id_existing", "id_new"])

    _add_matching_edges(matching_edges, m)


def _disable_leaflet_click_outline(m: folium.Map) -> None:
    m.get_root().header.add_child(folium.Element("""
    <style>
    .leaflet-interactive:focus {
        outline: none;
    }
    </style>
    """))


def _add_matching_edges(edges: GeoDataFrame, m: folium.Map) -> None:
    _inject_mouseover_effects(m)
    m.get_root().html.add_child(folium.Element(f"""
    <script>
    document.addEventListener("DOMContentLoaded", function () {{
        window.removedMatches = [];
        window.addedMatches = [];
        let selected = {{ existing: null, new: null }};
        let highlightedLayer = null;

        const map = window.{m.get_name()};

        const initialMatches = {edges.to_json(to_wgs84=True)};
        const matchLayer = L.geoJSON(initialMatches, {{
            style: function(feature) {{
                return {{ color: 'green', weight: 3 }};
            }},
            onEachFeature: function (feature, layer) {{
                // Allow users to remove initial matching edges
                layer.on("click", function () {{
                    map.removeLayer(layer);
                    window.removedMatches.push({{
                        id_existing: feature.properties.id_existing,
                        id_new: feature.properties.id_new
                    }});
                    console.log("Removed match:", feature.properties.id_existing, "→", feature.properties.id_new);
                }});
                applyLineHoverEffects(layer);
            }}
        }}).addTo(map);

        const initialMatchKeys = new Set(
            initialMatches.features.map(f => `${{f.properties.id_existing}}--${{f.properties.id_new}}`)
        );

        function matchExists(from_id, to_id) {{
            const key = `${{from_id}}--${{to_id}}`;
            if (initialMatchKeys.has(key) &&
                !window.removedMatches.some(m => m.id_existing === from_id && m.id_new === to_id)) {{
                return true;
            }}
            if (window.addedMatches.some(m => m.id_existing === from_id && m.id_new === to_id)) {{
                return true;
            }}
            return false;
        }}

        // Add new matches by clicking existing + new buildings
        map.eachLayer(layer => {{
            if (layer.feature && layer.feature.properties && layer.feature.geometry) {{
                const type = layer.feature.properties.type;
                const id = layer.feature.properties.index;
                const centroid = L.geoJSON(layer.feature).getBounds().getCenter();

                if (type === "existing" || type === "new") {{
                    layer.on("click", function () {{
                        const alreadySelected = selected[type]?.id === id;

                        if (alreadySelected) {{
                            selected[type] = null;
                            if (highlightedLayer && highlightedLayer._originalStyle) {{
                                highlightedLayer.setStyle(highlightedLayer._originalStyle);
                                highlightedLayer = null;
                            }}
                            return;
                        }}

                        if (highlightedLayer && highlightedLayer._originalStyle) {{
                            highlightedLayer.setStyle(highlightedLayer._originalStyle);
                        }}

                        selected[type] = {{
                            id: id,
                            latlng: centroid
                        }};

                        layer._originalStyle = {{
                            color: layer.options.color,
                            weight: layer.options.weight,
                            dashArray: layer.options.dashArray || null
                        }};

                        layer.setStyle({{
                            color: "gold",
                            weight: 5,
                            dashArray: "5,5"
                        }});
                        highlightedLayer = layer;

                        if (selected.existing && selected.new) {{
                            const from = selected.existing;
                            const to = selected.new;

                            if (matchExists(from.id, to.id)) {{
                                console.log("Skipped adding match (duplicate):", from.id, "→", to.id);
                                selected = {{ existing: null, new: null }};
                                if (highlightedLayer && highlightedLayer._originalStyle) {{
                                    highlightedLayer.setStyle(highlightedLayer._originalStyle);
                                    highlightedLayer = null;
                                }}
                                return;
                            }}

                            const line = L.polyline([from.latlng, to.latlng], {{
                                color: "green",
                                weight: 3
                            }}).addTo(map);

                            applyLineHoverEffects(line);

                            line.on("click", function () {{
                                map.removeLayer(line);
                                window.addedMatches = window.addedMatches.filter(match => match.layer !== line);
                                console.log("Removed added match:", from.id, "→", to.id);
                            }});

                            if (window.removedMatches.some(m => m.id_existing === from.id && m.id_new === to.id)) {{
                            window.removedMatches = window.removedMatches.filter(
                                m => !(m.id_existing === from.id && m.id_new === to.id)
                            );
                            console.log("Re-added removed match:", from.id, "→", to.id);
                            }} else {{
                                window.addedMatches.push({{
                                    id_existing: from.id,
                                    id_new: to.id,
                                    layer: line
                                }});
                                console.log("Added match:", from.id, "→", to.id);
                            }}

                            selected = {{ existing: null, new: null }};
                            if (highlightedLayer && highlightedLayer._originalStyle) {{
                                highlightedLayer.setStyle(highlightedLayer._originalStyle);
                                highlightedLayer = null;
                            }}
                        }}
                    }});
                }}
            }}
        }});
    }});
    </script>
    """
    ))


def _inject_mouseover_effects(m: folium.Map) -> None:
    m.get_root().html.add_child(folium.Element("""
    <script>
    function applyLineHoverEffects(line) {
        line.on("mouseover", function () {
            line.setStyle({ color: "lime", weight: 4 });
        });

        line.on("mouseout", function () {
            line.setStyle({ color: "green", weight: 3 });
        });
    }
    </script>
    """
    ))


def _add_legend(m: folium.Map, candidates_highlighted=False) -> None:
    candidates_entry = """
    <div>
        <span style="display: inline-block; width: 26px; height: 18px; position: relative; vertical-align: middle;">
            <i style="background: rgba(255, 127, 80, 0.2); width: 18px; height: 18px; display: inline-block;
                border: 3px solid orangered; position: absolute; top: 0; left: 0; z-index: 2;"></i>
            <i style="background: rgba(135, 206, 235, 0.5); width: 18px; height: 18px; display: inline-block;\
                border: 3px solid steelblue; position: absolute; top: 0; left: 6px; z-index: 1;"></i>
        </span>
        Candidate Pair
    </div>
    """

    matching_edges_entry = """
    <div>
        <i style="border-top: 3px solid green; width: 18px;
            display: inline-block; margin-right: 6px; vertical-align: middle;"></i>
        Matching Relationships
    </div>
    """

    m.get_root().html.add_child(folium.Element(f"""
    <div style="position: fixed; bottom: 30px; left: 30px; background: rgba(255, 255, 255, 0.8);
            border: 1px solid lightgrey; z-index: 9999; font-size: 14px; padding: 10px; line-height: 18px;">
        <b style="display: block; margin-bottom: 6px;">Building Layers</b>

        <div style="margin-bottom: 6px;">
            <i style="background: rgba(255, 127, 80, 0.2); width: 18px; height: 18px; display: inline-block;
                border: 2px solid coral; margin-right: 6px; vertical-align: middle;"></i>
            New Buildings
        </div>

        <div style="margin-bottom: 6px;">
            <i style="background: rgba(135, 206, 235, 0.5); width: 18px; height: 18px; display: inline-block;
                border: 2px solid skyblue; margin-right: 6px; vertical-align: middle;"></i>
            Existing Buildings
        </div>

        {candidates_entry if candidates_highlighted else matching_edges_entry}
    </div>
    """
    ))
