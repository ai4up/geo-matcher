import json
from pathlib import Path
from typing import Any, Callable, Optional

from geopandas import GeoDataFrame
import folium
import geopandas as gpd

from geo_matcher.state import State
from geo_matcher import spatial
from geo_matcher.candidate_pairs import CandidatePairs

BUILDING_LAYER_COLORS = {
    "map": {
        "new": {
            "default": {"color": "coral", "fillOpacity": 0.2, "fillColor": "coral", "weight": 2, "dashArray": None},
            "highlight": {"color": "orangered", "fillOpacity": 0.4, "fillColor": "coral", "weight": 4, "dashArray": "5, 8"},
        },
        "existing": {
            "default": {"color": "steelblue", "fillOpacity": 0.4, "fillColor": "skyblue", "weight": 2},
            "highlight": {"color": "steelblue", "fillOpacity": 0.6, "fillColor": "skyblue", "weight": 4},
        },
    },
    "satellite": {
        "new": {
            "default": {"color": "coral", "fillOpacity": 0, "weight": 3, "dashArray": None},
            "highlight": {"color": "red", "fillOpacity": 0, "weight": 6, "dashArray": "5, 8"},
        },
        "existing": {
            "default": {"color": "skyblue", "fillOpacity": 0, "weight": 3},
            "highlight": {"color": "skyblue", "fillOpacity": 0, "weight": 5},
        },
    },
}

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
    _add_stylized_buildings_layer(m, existing_buildings, "Existing Buildings", "existing", "A_candidate")
    _add_stylized_buildings_layer(m, new_buildings, "New Buildings", "new", "B_candidate")
    _add_tutorial_marker(m, lat, lon)
    _add_baselayer_marker(m)

    m.save(filepath)

def create_neighborhood_tutorial_html(filepath: str) -> None:
    """
    Create a demo Folium HTML map with an example neighborhood and an instruction text.
    """
    # Load demo data
    demo_data_path = Path(__file__).parent / "data" / "tutorial-neighborhood.pickle"
    data = CandidatePairs.load(demo_data_path)
    data.preliminary_matching_estimate()

    pairs = GeoDataFrame(data.pairs)
    pairs["geometry_existing"] = pairs["id_existing"].map(data.dataset_a.geometry)
    pairs["geometry_new"] = pairs["id_new"].map(data.dataset_b.geometry)

    # Initialize map and add demo buildings
    m = _initialize_map(44.8031, 3.42505, 20)
    _add_stylized_buildings_layer(m, data.dataset_a, "Existing Buildings", "existing")
    _add_stylized_buildings_layer(m, data.dataset_b, "New Buildings", "new")
    _add_legend(m)
    _disable_leaflet_click_outline(m)
    _inject_matching_relationships(m, pairs)
    _inject_custom_js(m)

    folium.LayerControl(collapsed=True).add_to(m)

    m.save(filepath)


def create_candidate_pair_html(state: State, id_existing: str, id_new: str, filepath: Path) -> None:
    """
    Create a Folium HTML map with a candidate pair.
    """
    if filepath.is_file():
        return

    candidate_pair = state.get_candidate_pair(id_existing, id_new)

    c = candidate_pair["geometry_new"].centroid
    existing_buildings = state.get_existing_buildings_at(c)
    new_buildings = state.get_new_building_at(c)

    lat, lon = spatial.to_lat_lon(c.x, c.y, existing_buildings.crs)
    m = _initialize_map(lat, lon, 20)

    _add_stylized_buildings_layer(m, existing_buildings, "Existing Buildings", "existing", id_existing)
    _add_stylized_buildings_layer(m, new_buildings, "New Buildings", "new", id_new)

    _add_legend(m, candidates_highlighted=True)
    _add_satellite_imagery_toogle(m)
    _disable_leaflet_click_outline(m)
    _inject_custom_js(m)

    folium.LayerControl(collapsed=True).add_to(m)

    m.save(filepath)


def create_neighborhood_html(state: State, id: str, filepath: Path) -> None:
    """
    Create a Folium HTML map with all candidate pairs in a neighborhood.
    """
    if filepath.is_file():
        return

    candidate_pairs = state.get_candidate_pairs(id)
    existing_buildings = state.get_existing_buildings(id)
    new_buildings = state.get_new_buildings(id)

    new_buildings = new_buildings.loc[candidate_pairs["id_new"].unique()]
    existing_buildings = existing_buildings.loc[candidate_pairs["id_existing"].unique()]

    lat, lon = spatial.center_lat_lon(candidate_pairs["geometry_new"])
    m = _initialize_map(lat, lon, 19)

    _add_stylized_buildings_layer(m, existing_buildings, "Existing Buildings", "existing")
    _add_stylized_buildings_layer(m, new_buildings, "New Buildings", "new")

    _add_legend(m)
    _add_satellite_imagery_toogle(m)
    _disable_leaflet_click_outline(m)
    _inject_matching_relationships(m, candidate_pairs)
    _inject_custom_js(m)

    folium.LayerControl(collapsed=True).add_to(m)

    m.save(filepath)


def _initialize_map(lat: float, lon: float, zoom_level: int) -> folium.Map:
    m = folium.Map(location=[lat, lon], zoom_start=zoom_level, tiles=None)

    # Highest resolution
    carto = folium.TileLayer(
        "CartoDB.Positron",
        name="CartoDB Positron",
        show=True,
    )
    # Familiar map style
    osm = folium.TileLayer(
        "OpenStreetMap",
        name="OpenStreetMap",
        show=False,
    )
    # Satellite imagery
    esri = folium.TileLayer(
        tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        attr='Esri',
        name='Esri Satellite',
        max_native_zoom=18,
        max_zoom=20,
        show=False,
    )
    # Base map without buildings
    esri_topo = folium.TileLayer(
        "Esri.WorldTopoMap",
        name="Esri WorldTopoMap",
        show=False,
        max_native_zoom=18,
        max_zoom=19,
    )

    carto.add_to(m)
    osm.add_to(m)
    esri.add_to(m)
    esri_topo.add_to(m)

    _inject_var(m, 'cartoPositron', carto.get_name())
    _inject_var(m, 'osm', osm.get_name())
    _inject_var(m, 'esriSatellite', esri.get_name())
    _inject_var(m, 'esriTopo', esri.get_name())

    return m


def _add_tutorial_marker(m: folium.Map, lat: float, lon: float) -> None:
    folium.Marker(
        location=[lat, lon],
        tooltip="Building pair to be labeled as 'Match' / 'No Match' / 'Unsure'.",
        icon=folium.Icon(color="lightred", icon="info-sign"),
    ).add_to(m)


def _add_baselayer_marker(m: folium.Map) -> None:
    folium.Marker(
        location=[44.80484, 3.34594],
        tooltip="Building from the baselayer. Can be ignored. Choose 'Esri WorldTopoMap' for a baselayer without buildings (lower resolution).",
        icon=folium.Icon(color="lightgray", icon="info-sign"),
    ).add_to(m)


def _add_stylized_buildings_layer(
    m: folium.Map,
    gdf: GeoDataFrame,
    layer_name: str,
    layer_ref: str,
    highlight_id: Optional[str] = None,
) -> None:
    def style_function(feature):
        is_highlight = highlight_id is not None and feature["properties"].get("index") == highlight_id
        color_scheme = BUILDING_LAYER_COLORS["map"][layer_ref]
        return color_scheme["highlight"] if is_highlight else color_scheme["default"]

    gdf["type"] = layer_ref

    feature_group = folium.FeatureGroup(name=layer_name)
    geojson = _create_buildings_layer(gdf, style_function)
    geojson.add_to(feature_group)
    feature_group.add_to(m)

    _inject_var(m, layer_ref, geojson.get_name())

    if highlight_id:
        _inject_var(m, layer_ref + "Highlighted", json.dumps(highlight_id))


def _create_buildings_layer(
    gdf: GeoDataFrame,
    style_function: Callable[[dict], dict],
) -> folium.GeoJson:
    def highlight_function(_):
        return {"fillOpacity": 0.8}

    if gdf.empty:
        return folium.GeoJson({"type": "FeatureCollection", "features": []})

    tooltip = folium.GeoJsonTooltip(fields=["index"], aliases=["Building ID"])
    features = folium.GeoJson(
        gdf.reset_index(),
        tooltip=tooltip,
        style_function=style_function,
        highlight_function=highlight_function,
    )

    return features


def _inject_matching_relationships(m: folium.Map, candidate_pairs: GeoDataFrame) -> None:
    matches = candidate_pairs[candidate_pairs["match"]]
    if matches.empty:
        matching_edges = gpd.GeoDataFrame(geometry=[], crs=candidate_pairs["geometry_existing"].crs)
    else:
        matching_edges = spatial.connect_with_lines(
            matches.set_index("id_existing")["geometry_existing"],
            matches.set_index("id_new")["geometry_new"]
        ).reset_index(names=["id_existing", "id_new"])

    _inject_var(m, "pairs", candidate_pairs[["id_existing", "id_new", "match"]].to_json(orient='records'))
    _inject_var(m, "initialMatches", matching_edges.to_json(to_wgs84=True))


def _disable_leaflet_click_outline(m: folium.Map) -> None:
    _inject_css(m, """
    .leaflet-interactive:focus {
        outline: none;
    }
    """)


def _add_satellite_imagery_toogle(m: folium.Map) -> None:
    _inject_var(m, "styleMap", json.dumps(BUILDING_LAYER_COLORS))
    _inject_css(m, """
    .satellite-toggle-button {
        position: absolute;
        top: 10px;
        right: 10px;
        z-index: 9999;
        background: white;
        padding: 6px 10px;
        border: 1px solid #ccc;
        cursor: pointer;
        font-size: 14px;
        border-radius: 4px;
    }
    .leaflet-control-layers {
        top: 40px !important;
        right: 10px !important;
    }
    """)
    _inject_element(m, """
    <div class="satellite-toggle-button" id="satelliteToggleButton">Switch to Satellite</div>
    """)


def _add_legend(m: folium.Map, candidates_highlighted=False) -> None:
    candidates_entry = """
    <div>
        <span style="display: inline-block; width: 26px; height: 18px; position: relative; vertical-align: middle;">
            <i style="background: rgba(255, 127, 80, 0.2); width: 18px; height: 18px; display: inline-block;
                border: 3px dotted orangered; position: absolute; top: 0; left: 0; z-index: 2;"></i>
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

    _inject_element(m, f"""
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
    """)


def _inject_element(m: folium.Map, element: str) -> None:
    m.get_root().html.add_child(folium.Element(element))


def _inject_var(m: folium.Map, name: str, data: Any) -> None:
    _inject_element(m, f"""
    <script>
    document.addEventListener("DOMContentLoaded", function () {{
        window.{name} = {data};
    }});
    </script>
    """)


def _inject_css(m: folium.Map, css: str) -> None:
    m.get_root().header.add_child(folium.Element(f"""
    <style>
    {css}
    </style>
    """))


def _inject_custom_js(m: folium.Map) -> None:
    _inject_var(m, 'map', m.get_name())
    _inject_element(m, """
      <script src="/static/js/map_custom.js"></script>
    """)
