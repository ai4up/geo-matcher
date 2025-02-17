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

    return jsonify({"message": "Success", "candidate": s.current_candidate_id() or ''})


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

    table_data = [{
            "base_name": row.base_name,  # or row['base_id'] if that's a column in your DataFrame
            "base_address": row.base_address,
            "base_country": row.base_country,
            "base_house_number": row.base_house_number,
            "base_normalized_phone": row.base_normalized_phone,
            "can_name": row.can_name,  # or row['can_id'] if that's a column in your DataFrame
            "can_address": row.can_address,
            "can_country": row.can_country,
            "can_house_number": row.can_house_number,
            "can_normalized_phone": row.can_normalized_phone,
            }
        for idx, row in s.candidates.loc[['08f2ab381ba7615403d9afedf3d41905']].iterrows()]
        #for idx, row in s.candidates.loc[[id]].iterrows()]  

    if next_id := s.next_candidate_id():
        app.logger.debug(f"Pre-generating HTML map for candidate {next_id}")
        executor.submit(_create_html, next_id)

    return render_template("show_candidate.html", label_function_script=_js_labeling_func(), id=id, table_data=table_data)


def _html_exists(id):
    return (maps_dir / f"candidate_{id}.html").is_file()


def _create_pop_up(row):
    #Setup the content of the popup
    iframe = folium.IFrame(f"Name:{row['can_name']} \nAddress: {row['can_address']}", width=300, height=80)
    
    #Initialise the popup using the iframe
    return folium.Popup(iframe, min_width=300, max_width=500)
    

def _create_html(id):
    if _html_exists(id):
        return
    
    #pairs = s.candidates.loc[[id]]
    pairs = s.candidates.loc[['08f2ab381ba7615403d9afedf3d41905']]
    print('Pairs:',len(pairs))
    base_coords = [pairs.iloc[0]['base_latitude'],pairs.iloc[0]['base_longitude']]
    # Create Folium map centered on base poi
    with warnings.catch_warnings():
        warnings.simplefilter(action='ignore', category=UserWarning)
        m = folium.Map(location=base_coords,zoom_start=19,tiles='Cartodb Positron')


    # display candidates per base_poi
    if len(s.candidates) > 1:
        marker_cluster = MarkerCluster().add_to(m)
    
        for _, row in pairs.iterrows():
            coords = [row['can_latitude']+1e-5, row['can_longitude']+1e-5] # Add a small offset to avoid overlapping markers
            folium.Marker(coords,
                        popup=_create_pop_up(row),
                        icon=folium.Icon(color='blue', icon=''),
                        opacity=0.8,
                        ).add_to(marker_cluster)

    else:
        coords = [pairs['can_latitude']+1e-5, pairs['can_longitude']+1e-5] # Add a small offset to avoid overlapping markers
        folium.Marker(coords,
                        popup=_create_pop_up(row),
                        icon=folium.Icon(color='red', icon=''),
                        ).add_to(marker_cluster)


    folium.Marker(base_coords,
                    popup=_create_pop_up(pairs.iloc[0]),
                    icon=folium.Icon(color='purple', icon=''),
                    ).add_to(m)            
    
    # Add the script to the map’s HTML
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
                window.location.href = `/show_candidate/${data.candidate}`;
            });
        }
    </script>
    """
    return func


def _clean_maps_dir():
    shutil.rmtree(maps_dir, ignore_errors=True)
    maps_dir.mkdir(parents=True, exist_ok=True)
