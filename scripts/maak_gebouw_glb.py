"""
Maakt een losse GLB van een of meer BAG-panden, voor de tool "Gebouw plaatsen" in LEIA.

Gebruikt de functies uit het meegeleverde script maak_3dbag_zeeland_3dtiles_v7.py:
CityJSON lezen, BuildingParts, earcut-triangulatie en de exacte RD/NAP naar ECEF omrekening.

Werking per pand:
1. De CityJSON-tegel met het pand zoeken: in je eigen map of zip (--cityjson),
   of de footprint opzoeken in de BAG (PDOK) en alleen die 3DBAG-tegel downloaden.
2. Het pand trianguleren zoals in v7 en omrekenen naar lokale meters (oost, noord, omhoog).
3. Het midden van de footprint op 0,0 zetten en de onderkant op hoogte 0.
4. Opslaan als static/models/NL.IMBAG.Pand.<id>.glb en de config-regel printen.

Voorbeeld:
python scripts/maak_gebouw_glb.py 0687100000000073
python scripts/maak_gebouw_glb.py 0687100000000073 0687100000000177 --cityjson C:/Users/naam/Downloads/tiles_zeeland.zip

Benodigde packages: dezelfde als het v7-script.
"""

import argparse
import importlib.util
import json
import zipfile
from pathlib import Path
from urllib.request import urlopen

import numpy as np
import trimesh
from shapely.geometry import Polygon
from shapely.ops import unary_union

REPO_DIR = Path(__file__).resolve().parents[1]
DEFAULT_V7_SCRIPT = REPO_DIR.parent / "docs" / "maak_3dbag_zeeland_3dtiles_v7.py"
DEFAULT_WORKDIR = REPO_DIR.parent / "3dbag_werkmap"
DEFAULT_OUTDIR = REPO_DIR / "static" / "models"

PDOK_PAND_URL = (
    "https://api.pdok.nl/kadaster/bag/ogc/v2/collections/pand/items"
    "?identificatie={}&crs=http://www.opengis.net/def/crs/EPSG/0/28992&f=json"
)


def load_v7_script(path):
    path = Path(path)
    if not path.exists():
        raise RuntimeError(f"v7-script niet gevonden: {path}. Geef het pad mee met --v7-script.")

    spec = importlib.util.spec_from_file_location("v7", path)
    v7 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(v7)
    return v7


def find_pand_in_bag(pand_number):
    with urlopen(PDOK_PAND_URL.format(pand_number)) as response:
        data = json.load(response)

    if not data["features"]:
        raise RuntimeError(f"Pand {pand_number} staat niet in de BAG.")

    return data["features"][0]


def footprint_bbox_rd(pand):
    # BAG-panden zijn altijd een Polygon, ring 0 is de buitenrand
    points = np.array(pand["geometry"]["coordinates"][0], dtype=np.float64)
    xmin, ymin = points.min(axis=0)
    xmax, ymax = points.max(axis=0)
    return (float(xmin), float(ymin), float(xmax), float(ymax))


def local_cityjson_texts(source):
    # Map met CityJSON-bestanden, of de zip zelf zodat uitpakken niet nodig is
    source = Path(source)
    if source.suffix == ".zip":
        with zipfile.ZipFile(source) as archive:
            for name in archive.namelist():
                if name.endswith(".city.json"):
                    yield archive.read(name).decode("utf-8")
    else:
        for path in sorted(source.rglob("*.json")):
            yield path.read_text(encoding="utf-8")


def downloaded_cityjson_texts(v7, pand_numbers, workdir):
    # Per pand de footprint opzoeken in de BAG (PDOK) en alleen die 3DBAG-tegel downloaden
    for pand_number in pand_numbers:
        pand = find_pand_in_bag(pand_number)
        for path in v7.download_cityjson_tiles(workdir, footprint_bbox_rd(pand)):
            yield Path(path).read_text(encoding="utf-8")


def find_buildings(cityjson_texts, object_ids):
    # Een keer door alle tegels en per pand de tegel bewaren waar het in staat.
    # Eerst op tekst zoeken, zodat niet elke tegel helemaal ingelezen hoeft te worden.
    found = {}
    for text in cityjson_texts:
        matches = [object_id for object_id in object_ids if object_id not in found and f'"{object_id}"' in text]
        if matches:
            cityjson = json.loads(text)
            for object_id in matches:
                found[object_id] = cityjson

        if len(found) == len(object_ids):
            break

    return found


def building_triangles(v7, cityjson, object_id):
    city_objects = cityjson["CityObjects"]
    building = city_objects[object_id]
    rd_vertices = v7.apply_cityjson_transform(cityjson)

    # Zelfde aanpak als v7: geometrie uit de BuildingParts, LoD2.2 waar mogelijk
    triangles = []
    for part in v7.get_building_geometries(object_id, building, city_objects):
        geometry = v7.choose_geometry(part)
        if not geometry:
            continue

        for surface in v7.iter_cityjson_surfaces_from_geometry(geometry):
            triangles.extend(v7.triangulate_surface_earcut(surface, rd_vertices))

    if not triangles:
        raise RuntimeError(f"{object_id} heeft geen bruikbare geometrie.")

    return rd_vertices, np.array(triangles, dtype=np.int64)


def rd_to_local_meters(v7, rd_vertices, triangles):
    # Alleen de punten van dit pand omrekenen, niet de hele tegel
    used = np.unique(triangles)
    x, y, z = v7.RD_NAP_TO_ECEF.transform(
        rd_vertices[used, 0],
        rd_vertices[used, 1],
        rd_vertices[used, 2],
    )
    ecef = np.column_stack([x, y, z]).astype(np.float64)

    local, _, _, _ = v7.ecef_to_local_enu(ecef, ecef.mean(axis=0))

    # Midden van de footprint op 0,0 en de onderkant op hoogte 0
    low = local.min(axis=0)
    high = local.max(axis=0)
    local[:, 0] -= (low[0] + high[0]) / 2
    local[:, 1] -= (low[1] + high[1]) / 2
    local[:, 2] -= low[2]

    # Driehoeken verwijzen nog naar de hele tegel, omzetten naar de nieuwe puntenlijst
    return local, np.searchsorted(used, triangles)


def add_double_sided_material(v7, glb_path):
    # trimesh schrijft bij vertexkleuren geen materiaal, dan gebruikt Cesium een metalen standaardmateriaal.
    # Dubbelzijdig net als in v7, anders vallen gevels weg door backface culling.
    gltf, bin_data = v7.read_glb(glb_path)
    gltf["materials"] = [{
        "pbrMetallicRoughness": {"metallicFactor": 0.0, "roughnessFactor": 1.0},
        "doubleSided": True,
    }]
    for mesh in gltf["meshes"]:
        for primitive in mesh["primitives"]:
            primitive["material"] = 0
    v7.write_glb(glb_path, gltf, bin_data)


def write_building_glb(v7, local_vertices, triangles, glb_path):
    vertices = []
    faces = []
    colors = []

    # Elke driehoek eigen punten en een grijstint op basis van de richting, zelfde look als de tileset
    for a, b, c in triangles:
        v0, v1, v2 = local_vertices[a], local_vertices[b], local_vertices[c]
        normal = np.cross(v1 - v0, v2 - v0)
        length = np.linalg.norm(normal)
        if length == 0:
            continue

        color = v7.styled_grey_from_normal(normal / length)
        start = len(vertices)
        vertices.extend([v0, v1, v2])
        faces.append([start, start + 1, start + 2])
        colors.extend([color, color, color])

    mesh = trimesh.Trimesh(
        vertices=np.array(vertices, dtype=np.float32),
        faces=np.array(faces, dtype=np.int64),
        process=False,
    )
    mesh.visual = trimesh.visual.ColorVisuals(mesh, vertex_colors=np.array(colors, dtype=np.uint8))

    glb_path.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(glb_path)
    add_double_sided_material(v7, glb_path)


def footprint_outline(local_vertices, triangles, margin=0.5):
    # Bovenaanzicht van het pand: alle driehoeken plat op de grond samengevoegd, met een kleine marge.
    # De viewer haalt binnen deze omtrek de bestaande bebouwing weg.
    flat = [Polygon(local_vertices[triangle][:, :2]) for triangle in triangles]
    outline = unary_union([p for p in flat if p.area > 0.01])
    outline = outline.buffer(margin, join_style="mitre").simplify(0.2)

    # Losse delen: dan de omhullende als een stuk
    if outline.geom_type != "Polygon":
        outline = outline.convex_hull

    return [[round(float(x), 1), round(float(y), 1)] for x, y in outline.exterior.coords[:-1]]


def make_building_glb(v7, cityjson, object_id, outdir):
    pand_number = object_id.split(".")[-1]
    print(f"\n{object_id}")

    rd_vertices, triangles = building_triangles(v7, cityjson, object_id)
    local_vertices, local_triangles = rd_to_local_meters(v7, rd_vertices, triangles)

    glb_path = outdir / f"{object_id}.glb"
    write_building_glb(v7, local_vertices, local_triangles, glb_path)
    print(f"Opgeslagen: {glb_path}")

    bouwjaar = cityjson["CityObjects"][object_id].get("attributes", {}).get("oorspronkelijkbouwjaar")
    return {
        "title": f"Pand {pand_number} ({bouwjaar})",
        "url": f"models/{object_id}.glb",
        "footprint": footprint_outline(local_vertices, local_triangles),
    }


def main():
    parser = argparse.ArgumentParser(description="Maak losse GLB-modellen van BAG-panden.")
    parser.add_argument("bag_ids", nargs="+", help="BAG pand-id, bijvoorbeeld 0687100000000073")
    parser.add_argument("--v7-script", default=DEFAULT_V7_SCRIPT, help="Pad naar maak_3dbag_zeeland_3dtiles_v7.py")
    parser.add_argument("--workdir", default=DEFAULT_WORKDIR, help="Map voor gedownloade CityJSON (niet in git)")
    parser.add_argument("--outdir", default=DEFAULT_OUTDIR, help="Map voor de GLB-bestanden")
    parser.add_argument("--cityjson", help="Map of zip met CityJSON die je al hebt, dan wordt er niks gedownload")
    args = parser.parse_args()

    v7 = load_v7_script(args.v7_script)
    v7.ensure_proj_grid_files()
    v7.RD_NAP_TO_ECEF = v7.check_exact_vertical_transform()

    object_ids = [f"NL.IMBAG.Pand.{bag_id.split('.')[-1]}" for bag_id in args.bag_ids]
    if args.cityjson:
        texts = local_cityjson_texts(args.cityjson)
    else:
        pand_numbers = [object_id.split(".")[-1] for object_id in object_ids]
        texts = downloaded_cityjson_texts(v7, pand_numbers, Path(args.workdir))

    print("CityJSON doorzoeken...")
    tiles = find_buildings(texts, object_ids)

    entries = []
    for object_id in object_ids:
        if object_id not in tiles:
            print(f"\n{object_id} niet gevonden in de CityJSON, overgeslagen.")
            continue
        entries.append(make_building_glb(v7, tiles[object_id], object_id, Path(args.outdir)))

    print("\nZet dit in de config bij de tool placeBuilding, onder settings.buildings:")
    print(",\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries))


if __name__ == "__main__":
    main()
