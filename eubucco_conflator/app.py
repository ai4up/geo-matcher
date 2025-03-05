import atexit
import warnings
import webbrowser
from pathlib import Path
import shutil

from flask import Flask, render_template, request, jsonify
from flask_executor import Executor
import folium
from folium.plugins import MarkerCluster
from waitress import serve

from eubucco_conflator.state import State as s
from eubucco_conflator.state import RESULTS_FILE

FT = ['name','address','house_number','country','phone_number']
prefix = ['base','can']


app = Flask(__name__)
app.url_map.strict_slashes = False
executor = Executor(app)
maps_dir = Path(app.static_folder) / "maps"


def start():
    _clean_maps_dir()
    atexit.register(s.store_results)

    webbrowser.open(f"http://127.0.0.1:5001/show_candidate")
    serve(app, host="127.0.0.1", port=5001)


@app.route("/")
def home():
    return render_template("index.html")


@app.route('/store-label', methods=['POST'])
def store_label():
    data = request.json
    print(data)
    id = data.get("id")
    label = data.get("label")
    existing_id = data.get("existing_id")
    s.add_result(id, label, existing_id)
    
    new_id = s.current_candidate_id() or ""
    
    if id == new_id:
        return jsonify({"message": "Success"})

    return jsonify({"message": "Success", "candidate": new_id})


@app.route("/show_candidate")
@app.route("/show_candidate/<id>")
def show_candidate(id=None):
    if id is None:
        id = s.current_candidate_id()

    if id is None:
        s.store_results()
        return f"All places labeled! Results stored in {RESULTS_FILE}", 200

    if id not in s.candidates.index:
        return "Candidate not found", 404

    _create_html(id)

    def _create_table():
        # todo: refactor this to be more readable
        # todo: allow table values that exist per base and candidate, as well as common ones, like match_type or features
        prefixes = ['base','can']
        columns = ['name', 'address', 'house_number', 'country', 'normalized_phone']

        table_data_cols = [
                {
                    f"{prefix}_{col}": row[f"{prefix}_{col}"]
                    for prefix in prefixes
                    for col in columns
                    #if f"{prefix}_{col}" in row
                }
                for _, row in s.candidates.loc[[id]].iterrows()
            ]
        
        table_data_fix_vals = [{
                f"{prefixes[1]}_latitude": row[f'{prefixes[1]}_latitude'],
                f"{prefixes[1]}_longitude": row[f'{prefixes[1]}_longitude'],
                f"{prefixes[1]}_id": row[f'{prefixes[1]}_id'],
                } 
                for idx, row in s.candidates.loc[[id]].iterrows()]
        
        return [table_data_cols[0] | table_data_fix_vals[0]]


    table_data = _create_table()
    
    # table_data = [{
    #         "base_name": row['base_name'], # or row['base_id'] if that's a column in your DataFram'
    #         "base_address": row['base_address'],
    #         "base_country": row['base_country'],
    #         "base_house_number": row['base_house_number'],
    #         "base_normalized_phone": row['base_normalized_phone'],
    #         "can_id": row['can_id'], 
    #         "can_name": row['can_name'], 
    #         "can_address": row['can_address'],
    #         "can_country": row['can_country'],
    #         "can_house_number": row['can_house_number'],
    #         "can_normalized_phone": row['can_normalized_phone'],
    #         "can_latitude": row['can_latitude'],
    #         "can_longitude": row['can_longitude'],
    #         }
    #         for idx, row in s.candidates.loc[[id]].iterrows()]  


    if next_id := s.next_candidate_id():
        app.logger.debug(f"Pre-generating HTML map for candidate {next_id}")
        executor.submit(_create_html, next_id)

    return render_template("show_candidate.html", label_function_script=_js_labeling_func(), id=id, table_data=table_data)


def _html_exists(id):
    return (maps_dir / f"candidate_{id}.html").is_file()


def _create_pop_up(row):
    iframe = folium.IFrame(f"Name:{row['can_name']} \nAddress: {row['can_address']}", width=300, height=80)
    return folium.Popup(iframe, min_width=300, max_width=500)


def _adjust_zoom_level(pairs, base_coords):
    # Compute the maximum differences in latitude and longitude from the base POI
    max_lat_diff = (pairs['can_latitude'] - base_coords[0]).abs().max()
    max_lon_diff = (pairs['can_longitude'] - base_coords[1]).abs().max()
    delta = max(max_lat_diff, max_lon_diff)

    # Multiply delta by 2 to get a bounding box one zoom level less (more zoomed out)
    scale_factor = 2
    delta_scaled = delta * scale_factor

    # Build a bounding box that is symmetric around the base POI.
    min_bound = [base_coords[0] - delta_scaled, base_coords[1] - delta_scaled]
    max_bound = [base_coords[0] + delta_scaled, base_coords[1] + delta_scaled]
    return [min_bound, max_bound]


def _create_html(id):
    if _html_exists(id):
        return
    pairs = s.candidates.loc[[id]]
    base_coords = [pairs.iloc[0]['base_latitude'],pairs.iloc[0]['base_longitude']]
    
    # Create Folium map centered on base poi
    with warnings.catch_warnings():
        warnings.simplefilter(action='ignore', category=UserWarning)
        m = folium.Map(location=base_coords,tiles='Cartodb Positron')
        m.fit_bounds(_adjust_zoom_level(pairs, base_coords))

    # display candidates per base_poi
    if len(s.candidates) > 1:
        for _, row in pairs.iterrows():
            coords = [row['can_latitude']+1e-5, row['can_longitude']+1e-5] # Add a small offset to avoid overlapping markers
            folium.Marker(coords,
                        popup=_create_pop_up(row),
                        icon=folium.Icon(color='blue', icon=''),
                        opacity=0.8,
                        ).add_to(m)

    else:
        coords = [pairs['can_latitude']+1e-5, pairs['can_longitude']+1e-5] # Add a small offset to avoid overlapping markers
        folium.Marker(coords,
                        popup=_create_pop_up(row),
                        icon=folium.Icon(color='red', icon=''),
                        ).add_to(m)


    folium.Marker(base_coords,
                    popup=_create_pop_up(pairs.iloc[0]),
                    icon=folium.Icon(color='purple', icon=''),
                    ).add_to(m)            
    
    # Inject assignment of map_id to to window.map
    map_id = m.get_name()
    m.get_root().script.add_child(folium.Element(f"""
    console.log("Injecting map variable for: {map_id}");
    window.map = '{map_id}';
    """))

    m.get_root().html.add_child(folium.Element(_js_labeling_func()))
    m.save(maps_dir / f"candidate_{id}.html")


def _js_labeling_func():
    func = """
    <script>
        // Global function accessible from popups
        function labelPair(id, label, existing_id) {
            console.log("Saved id:", id);
            fetch('/store-label', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    id: id,
                    label: label,
                    existing_id: existing_id
                })
            })
            .then(response => response.json())
            .then(data => {
                console.log("Saved:", data);
                if (data.hasOwnProperty('candidate')) {
                    window.location.href = `/show_candidate/${data.candidate}`;
                }
            });
        }
    </script>
    """
    return func


def _clean_maps_dir():
    shutil.rmtree(maps_dir, ignore_errors=True)
    maps_dir.mkdir(parents=True, exist_ok=True)
