"""3DBAG Zeeland -> losse GLB per BAG-pand (op basis van Marco's v7-pipeline).

Gebruik:
    python maak_3dbag_zeeland_per_gebouw_glb.py --limit 1 --max-buildings 5
    python maak_3dbag_zeeland_per_gebouw_glb.py --skip-download --bag-id 0123456789012345
    python maak_3dbag_zeeland_per_gebouw_glb.py --skip-download

Input: 3DBAG CityJSON (download of hergebruik vanuit cityjson/).
Output: gebouwen/*.glb + gebouwen/manifest.json + gebouwen_index.jsonl, één GLB per Building/BAG-pand.

Behouden uit v7: LoD2.2/BuildingPart-selectie, typebewuste surfaces,
Earcut-triangulatie, grijze vertexkleuren en exacte EPSG:7415 -> 4978.

LET OP: de GLB's zijn losse, lokaal georiënteerde Y-up modellen (meters),
met de grond bij Y=0. De ligging, BAG-eigenschappen en bron staan in de
JSONL-index en manifest. Metadata staat ook in GLB asset.extras en node.extras.
Het zijn GEEN wereldgeplaatste 3D Tiles of B3DM bestanden.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import shutil
import struct
import zipfile
from pathlib import Path
from urllib.request import urlretrieve
from urllib.parse import urlsplit

import math
import os
import numpy as np
import trimesh
from pyproj import Transformer, datadir
from pyproj.transformer import TransformerGroup
from shapely.geometry import Point
from shapely.prepared import prep

# Afkomstig uit het v7-script van de opdrachtgever.
VERSION = "v20250903"
TILE_INDEX_URL = f"https://data.3dbag.nl/{VERSION}/tile_index.fgb"
ZEELAND_BBOX_RD = (10000, 355000, 100000, 425000)
REQUIRED_PROJ_GRIDS = {
    "nl_nsgi_nlgeo2018.tif": "https://cdn.proj.org/nl_nsgi_nlgeo2018.tif",
    "nl_nsgi_rdcorr2018.tif": "https://cdn.proj.org/nl_nsgi_rdcorr2018.tif",
    "nl_nsgi_rdtrans2018.tif": "https://cdn.proj.org/nl_nsgi_rdtrans2018.tif",
}
DEFAULT_OUTDIR = Path(__file__).resolve().parent / "3dbag_output"
DEFAULT_BOUNDARY = Path(__file__).resolve().parent / "Provinciegrens.gpkg"
RD_NAP_TO_ECEF = None
ECEF_TO_LONLAT = Transformer.from_crs("EPSG:4978", "EPSG:4979", always_xy=True)

# Labels, BAG-informatie en 3DBAG eigenschappen zijn uit v7 overgenomen.
PROPERTY_DEFS = {
    "bag_id": ("BAG pand ID", "identificatie"),
    "bouwjaar": ("Bouwjaar", "oorspronkelijkbouwjaar"),
    "status": ("Status", "status"),
    "begin_geldigheid": ("Begin geldigheid", "begingeldigheid"),
    "documentdatum": ("Documentdatum", "documentdatum"),
    "documentnummer": ("Documentnummer", "documentnummer"),
    "geconstateerd": ("Geconstateerd", "geconstateerd"),

    "daktype": ("Daktype", "b3_dak_type"),
    "reconstructiemethode": ("Reconstructiemethode", "b3_extrusie"),
    "aantal_bouwlagen": ("Aantal bouwlagen", "b3_bouwlagen"),

    "maaiveldhoogte": ("Maaiveldhoogte", "b3_h_maaiveld"),
    "dakhoogte_min": ("Minimale dakhoogte", "b3_h_dak_min"),
    "dakhoogte_50p": ("Dakhoogte 50p", "b3_h_dak_50p"),
    "dakhoogte_70p": ("Dakhoogte 70p", "b3_h_dak_70p"),
    "dakhoogte_max": ("Maximale dakhoogte", "b3_h_dak_max"),
    "nokhoogte": ("Nokhoogte", "b3_h_nok"),

    "grondoppervlakte": ("Grondoppervlakte", "b3_opp_grond"),
    "oppervlakte_plat_dak": ("Oppervlakte plat dak", "b3_opp_dak_plat"),
    "oppervlakte_schuin_dak": ("Oppervlakte schuin dak", "b3_opp_dak_schuin"),
    "oppervlakte_buitenmuur": ("Oppervlakte buitenmuur", "b3_opp_buitenmuur"),
    "oppervlakte_scheidingsmuur": ("Oppervlakte scheidingsmuur", "b3_opp_scheidingsmuur"),

    "volume_lod22": ("Volume LoD2.2", "b3_volume_lod22"),
    "glazen_dak": ("Glazen dak", "b3_is_glas_dak"),
    "kas_warenhuis": ("Kas/warenhuis", "b3_kas_warenhuis"),
    "kwaliteitsindicator": ("Kwaliteitsindicator", "b3_kwaliteitsindicator"),

    "bron_puntenwolk": ("Bron puntenwolk", "b3_pw_bron"),
    "datum_puntenwolk": ("Datum puntenwolk", "b3_pw_datum"),
    "puntenwolk_onvoldoende": ("Puntenwolk onvoldoende", "b3_pw_onvoldoende"),
    "selectiereden_puntenwolk": ("Selectiereden puntenwolk", "b3_pw_selectie_reden"),

    "nodata_fractie_ahn3": ("NoData fractie AHN3", "b3_nodata_fractie_ahn3"),
    "nodata_fractie_ahn4": ("NoData fractie AHN4", "b3_nodata_fractie_ahn4"),
    "nodata_fractie_ahn5": ("NoData fractie AHN5", "b3_nodata_fractie_ahn5"),

    "rmse_lod22": ("RMSE LoD2.2", "b3_rmse_lod22"),
}


# --- Geometrie, BuildingParts en metadata: oorspronkelijke v7-methode ---

def apply_cityjson_transform(cj):
    vertices = np.array(cj["vertices"], dtype=np.float64)
    transform = cj.get("transform")
    if transform:
        scale = np.array(transform["scale"], dtype=np.float64)
        translate = np.array(transform["translate"], dtype=np.float64)
        vertices = vertices * scale + translate
    return vertices


def is_ring(obj):
    return isinstance(obj, list) and obj and all(isinstance(x, int) for x in obj)


def get_lod_value(geom: dict):
    """Return LoD als float voor sortering. LoD kan string of nummer zijn."""
    try:
        return float(geom.get("lod", -1))
    except (TypeError, ValueError):
        return -1.0


def pick_best_geometry(city_object):
    """
    Kies de beste geometrie voor 3DBAG.

    Voorkeur:
    1. LoD 2.2
    2. hoogste beschikbare LoD >= 2
    3. hoogste beschikbare LoD
    """
    geoms = city_object.get("geometry", [])
    if not geoms:
        return None

    # Exact LoD 2.2 eerst.
    for geom in geoms:
        if str(geom.get("lod")) == "2.2":
            return geom

    # Daarna hoogste LoD >= 2.
    lod2 = [geom for geom in geoms if get_lod_value(geom) >= 2.0]
    if lod2:
        return sorted(lod2, key=get_lod_value)[-1]

    # Fallback: hoogste beschikbare LoD.
    return sorted(geoms, key=get_lod_value)[-1]


def iter_cityjson_surfaces_from_geometry(geom):
    """
    Typebewuste CityJSON surface iterator.

    Waarom:
    De oude recursieve collect_surfaces() liep blind door boundaries. Dat ging vaak goed,
    maar bij 3DBAG Solid/CompositeSurface/MultiSurface kan dat leiden tot gemiste vlakken
    of verkeerd geïnterpreteerde shells. Deze functie volgt expliciet de CityJSON-structuur:

    - MultiSurface / CompositeSurface:
        boundaries = [surface, surface, ...]
    - Solid:
        boundaries = [shell, shell, ...]
        shell = [surface, surface, ...]
    - MultiSolid / CompositeSolid:
        boundaries = [solid, solid, ...]
        solid = [shell, shell, ...]
    """
    boundaries = geom.get("boundaries", [])
    geom_type = geom.get("type")

    if geom_type in ("MultiSurface", "CompositeSurface"):
        for surface in boundaries:
            if is_valid_surface(surface):
                yield surface
        return

    if geom_type == "Solid":
        for shell in boundaries:
            if not isinstance(shell, list):
                continue
            for surface in shell:
                if is_valid_surface(surface):
                    yield surface
        return

    if geom_type in ("MultiSolid", "CompositeSolid"):
        for solid in boundaries:
            if not isinstance(solid, list):
                continue
            for shell in solid:
                if not isinstance(shell, list):
                    continue
                for surface in shell:
                    if is_valid_surface(surface):
                        yield surface
        return

    # Conservatieve fallback voor onbekende/afwijkende CityJSON-structuren.
    yield from iter_surfaces_fallback(boundaries)


def is_valid_surface(surface):
    """Een CityJSON surface is een lijst met één of meer rings."""
    if not isinstance(surface, list) or not surface:
        return False
    return all(is_ring(ring) for ring in surface)


def iter_surfaces_fallback(obj):
    """
    Fallback die alleen echte surfaces oplevert.
    Deze wordt alleen gebruikt voor onbekende geometry types.
    """
    if not isinstance(obj, list) or not obj:
        return

    if is_valid_surface(obj):
        yield obj
        return

    for child in obj:
        yield from iter_surfaces_fallback(child)


def newell_normal(points3d):
    """Robuuste normaal voor polygonen met meer dan 3 punten."""
    normal = np.zeros(3, dtype=np.float64)
    n = len(points3d)

    for i in range(n):
        p0 = points3d[i]
        p1 = points3d[(i + 1) % n]
        normal[0] += (p0[1] - p1[1]) * (p0[2] + p1[2])
        normal[1] += (p0[2] - p1[2]) * (p0[0] + p1[0])
        normal[2] += (p0[0] - p1[0]) * (p0[1] + p1[1])

    norm = np.linalg.norm(normal)
    if norm == 0:
        return np.array([0.0, 0.0, 1.0], dtype=np.float64)

    return normal / norm


def make_plane_basis(normal):
    """
    Maak twee assen in het vlak van de surface.
    Dit is stabieler dan simpelweg X/Y/Z droppen bij verticale gevels.
    """
    normal = normal / np.linalg.norm(normal)

    if abs(normal[2]) < 0.9:
        ref = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    else:
        ref = np.array([1.0, 0.0, 0.0], dtype=np.float64)

    u = np.cross(ref, normal)
    u_norm = np.linalg.norm(u)
    if u_norm == 0:
        u = np.array([1.0, 0.0, 0.0], dtype=np.float64)
    else:
        u = u / u_norm

    v = np.cross(normal, u)
    v = v / np.linalg.norm(v)

    return u, v


def choose_geometry(city_object):
    # Backwards-compatible naam; gebruikt nu de expliciete beste geometrie-selectie.
    return pick_best_geometry(city_object)


def clean_ring_indices(ring):
    if len(ring) > 1 and ring[0] == ring[-1]:
        return ring[:-1]
    return ring


def triangulate_surface_earcut(surface, rd_vertices):
    """
    Trianguleert één CityJSON-surface.

    Verbeteringen t.o.v. v6:
    - gebruikt een lokaal 2D vlak op basis van de polygon-normaal;
    - werkt beter voor verticale gevels;
    - ondersteunt holes;
    - valt voor simpele quads/triangles terug op fan-triangulatie als earcut faalt.
    """
    rings = []
    for ring in surface:
        ring = clean_ring_indices(ring)
        if len(ring) >= 3:
            rings.append(ring)

    if not rings:
        return []

    all_indices = [idx for ring in rings for idx in ring]
    pts3d = rd_vertices[all_indices, :3].astype(np.float64)

    outer_points = rd_vertices[rings[0], :3].astype(np.float64)
    normal = newell_normal(outer_points)
    u, v = make_plane_basis(normal)

    origin = outer_points.mean(axis=0)
    rel = pts3d - origin
    pts2d = np.column_stack([rel @ u, rel @ v]).astype(np.float64)

    ring_ends = []
    total = 0
    for ring in rings:
        total += len(ring)
        ring_ends.append(total)

    ring_ends = np.asarray(ring_ends, dtype=np.uint32)

    try:
        tris_local = earcut.triangulate_float64(pts2d, ring_ends)
    except Exception:
        tris_local = []

    triangles = []
    for i in range(0, len(tris_local), 3):
        a = all_indices[int(tris_local[i])]
        b = all_indices[int(tris_local[i + 1])]
        c = all_indices[int(tris_local[i + 2])]
        if a != b and b != c and a != c:
            triangles.append([a, b, c])

    # Fallback voor eenvoudige surfaces zonder holes.
    if not triangles and len(rings) == 1:
        ring = rings[0]
        triangles = [[ring[0], ring[i], ring[i + 1]] for i in range(1, len(ring) - 1)]

    return triangles


def collect_indices_from_boundaries(boundaries):
    indices = set()

    def walk(obj):
        if isinstance(obj, int):
            indices.add(obj)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(boundaries)
    return indices


def cityobject_centroid_rd(city_object, rd_vertices):
    geom = choose_geometry(city_object)
    if not geom:
        return None

    indices = collect_indices_from_boundaries(geom.get("boundaries", []))
    if not indices:
        return None

    coords = rd_vertices[list(indices), :2]
    return Point(float(coords[:, 0].mean()), float(coords[:, 1].mean()))


def get_attr(attrs: dict, source_field: str, fallback=""):
    value = attrs.get(source_field)
    if value is not None:
        return value
    return fallback

def clean_value(value):
    if value is None:
        return ""

    if isinstance(value, float):
        if math.isnan(value):
            return ""
        return float(value)

    if isinstance(value, (int, np.integer)):
        return int(value)

    if isinstance(value, (float, np.floating)):
        return float(value)

    if isinstance(value, bool):
        return bool(value)

    return str(value)

def feature_properties(obj_id: str, obj: dict):
    attrs = obj.get("attributes", {}) or {}

    props = {}
    for key, (label, source_field) in PROPERTY_DEFS.items():
        value = get_attr(attrs, source_field, "")
        props[key] = clean_value(value)

    if not props.get("bag_id"):
        props["bag_id"] = obj_id

    return props


def json_safe(value):
    """Maak bronattributen JSON-veilig zonder ontbrekende velden te verzinnen."""
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, (np.integer, np.bool_)):
        return value.item()
    if isinstance(value, (float, np.floating)):
        v = float(value)
        return v if math.isfinite(v) else None
    if isinstance(value, dict):
        return {str(key): json_safe(v) for key, v in value.items()}
    if isinstance(value, (tuple, list, np.ndarray)):
        return [json_safe(v) for v in value]
    return str(value)


def enu_axes_from_ecef(center_ecef):
    lon_deg, lat_deg, _ = ECEF_TO_LONLAT.transform(
        float(center_ecef[0]),
        float(center_ecef[1]),
        float(center_ecef[2]),
    )

    lon = math.radians(lon_deg)
    lat = math.radians(lat_deg)

    east = np.array([-math.sin(lon), math.cos(lon), 0.0])
    north = np.array([
        -math.sin(lat) * math.cos(lon),
        -math.sin(lat) * math.sin(lon),
        math.cos(lat),
    ])
    up = np.array([
        math.cos(lat) * math.cos(lon),
        math.cos(lat) * math.sin(lon),
        math.sin(lat),
    ])

    return east, north, up


def ecef_to_local_enu(ecef_vertices, center_ecef):
    east, north, up = enu_axes_from_ecef(center_ecef)
    delta = ecef_vertices - center_ecef
    local = np.column_stack([
        delta @ east,
        delta @ north,
        delta @ up,
    ])
    return local.astype(np.float32), east, north, up



def styled_grey_from_normal(normal):
    light_dir = np.array([-0.45, 0.35, 0.82], dtype=np.float64)
    light_dir = light_dir / np.linalg.norm(light_dir)

    shade = max(0.0, float(np.dot(normal, light_dir)))
    upness = max(0.0, float(normal[2]))

    brightness = 0.34 + 0.46 * shade + 0.20 * upness
    grey = int(np.clip(155 * brightness, 55, 175))
    return [grey, grey, grey, 255]



def get_building_geometries(obj_id, obj, city_objects):
    """
    Geeft de geometrie-objecten voor een BAG-pand terug.

    Belangrijk:
    - De parent Building bevat de goede attributen.
    - De echte LoD2.2-geometrie zit vaak in children / BuildingParts:
      NL.IMBAG.Pand.xxxxx-0, -1, ...
    - De parent Building kan soms alleen een groot footprint/baseplate-vlak bevatten.
      Daarom gebruiken we child/prefix-geometrie als die beschikbaar is, en de parent
      alleen als fallback.
    """
    collected = []
    seen_ids = set()

    def add_by_id(candidate_id):
        if candidate_id in seen_ids:
            return

        candidate = city_objects.get(candidate_id)
        if candidate is None:
            return

        seen_ids.add(candidate_id)
        collected.append(candidate)

        for child_id in candidate.get("children", []):
            add_by_id(child_id)

    # 1. Officiele children van de parent Building.
    for child_id in obj.get("children", []):
        add_by_id(child_id)

    # 2. Fallback voor 3DBAG IDs zoals NL.IMBAG.Pand.xxx-0, -1, ...
    prefix = obj_id + "-"
    for other_id in city_objects.keys():
        if other_id.startswith(prefix):
            add_by_id(other_id)

    # Gebruik alleen objecten die echt geometrie hebben.
    child_geometries = [candidate for candidate in collected if choose_geometry(candidate)]

    if child_geometries:
        return child_geometries

    # Alleen als er geen child-geometrie is: parent als fallback.
    return [obj]

def building_centroid_rd_from_geometries(obj_id, obj, city_objects, rd_vertices):
    indices = set()

    for geom_obj in get_building_geometries(obj_id, obj, city_objects):
        geom = choose_geometry(geom_obj)
        if not geom:
            continue

        indices.update(
            collect_indices_from_boundaries(
                geom.get("boundaries", [])
            )
        )

    if not indices:
        return None

    coords = rd_vertices[list(indices), :2]
    return Point(float(coords[:, 0].mean()), float(coords[:, 1].mean()))




class _EarcutProxy:
    @staticmethod
    def triangulate_float64(vertices, ring_ends):
        import mapbox_earcut
        return mapbox_earcut.triangulate_float64(vertices, ring_ends)

earcut = _EarcutProxy()


# --- GLB-export per gebouw ---

def safe_filename(text: str) -> str:
    """Houd één BAG-pand per herkenbaar, platformonafhankelijk bestand."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text)).strip("._") or "onbekend_pand"


def normalize_bag_id(value: str) -> str:
    v = str(value).strip().casefold()
    prefix = "nl.imbag.pand."
    if v.startswith(prefix):
        v = v[len(prefix):]
    return v


def matches_bag_id(obj_id: str, obj: dict, wanted: set[str] | None) -> bool:
    if wanted is None:
        return True
    values = [obj_id, obj.get("attributes", {}).get("identificatie", "")]
    return any(normalize_bag_id(v) in wanted for v in values if v)


def unpack_if_needed(path: Path) -> Path:
    if path.suffix.lower() == ".gz":
        target = path.with_suffix("")
        if not target.exists():
            with gzip.open(path, "rb") as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
        return target
    if path.suffix.lower() == ".zip":
        target_dir = path.parent / path.stem
        target_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path) as z:
            # Geen willekeurige bestanden uit de ZIP uitpakken: alleen CityJSON.
            members = [m for m in z.infolist() if not m.is_dir() and m.filename.lower().endswith(".json")]
            if not members:
                raise ValueError(f"Geen CityJSON in {path}")
            for m in members:
                name = Path(m.filename).name
                dest = target_dir / name
                if not dest.exists():
                    with z.open(m) as src, dest.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
        files = sorted(target_dir.glob("*.json"))
        return files[0]
    return path


def download_cityjson_tiles(outdir: Path, bbox, limit: int = 0) -> list[Path]:
    # FlatGeoBuf wordt pas vereist wanneer download gewenst is.
    try:
        import flatgeobuf as fgb
    except ImportError as exc:
        raise RuntimeError("Voor downloaden is flatgeobuf nodig: python -m pip install flatgeobuf") from exc
    cityjson_dir = outdir / "cityjson"
    cityjson_dir.mkdir(parents=True, exist_ok=True)
    print("3DBAG tile-index downloaden/lezen...")
    selected = []
    for tile in fgb.HTTPReader(TILE_INDEX_URL, bbox=bbox):
        url = tile.properties.get("cj_download")
        if not url:
            continue
        filename = cityjson_dir / Path(urlsplit(url).path).name
        if not filename.exists():
            print("Download:", filename.name)
            urlretrieve(url, filename)
        selected.append(unpack_if_needed(filename))
        # Ook de download beperken tijdens tests: --limit 5 downloadt maximaal vijf tegels.
        if limit and len(selected) >= limit:
            break
    return selected


def load_boundary(path: Path):
    if not path.exists():
        # Bewust duidelijk melden: bbox is NIET exact de provinciegrens.
        print("LET OP: Provinciegrens.gpkg ontbreekt. Er wordt alleen op RD-bbox gefilterd.")
        print("       Lever --boundary PAD op voor een exacte provinciegrensfilter.")
        return None
    import geopandas as gpd
    print("Provinciegrens laden:", path)
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        raise RuntimeError("Provinciegrens.gpkg heeft geen CRS")
    return prep(gdf.to_crs("EPSG:28992").geometry.union_all())


def ensure_exact_transformer() -> Transformer:
    """Laad PROJ-grids uit één expliciete gegevensmap (ook betrouwbaar op Windows).

    Alleen TIFF's in de Windows-gebruikersmap plaatsen is niet altijd voldoende:
    PROJ kan ze dan nog als 'missing' zien. We combineren de projectdatabase
    van pyproj en de drie officiële NSGI-grids daarom in één lokaal pad en
    wijzen pyproj expliciet naar dat pad, zonder de installatie te wijzigen.
    """
    user_dir = Path(datadir.get_user_data_dir())
    user_dir.mkdir(parents=True, exist_ok=True)
    for filename, url in REQUIRED_PROJ_GRIDS.items():
        target = user_dir / filename
        if not target.is_file():
            print("PROJ-grid downloaden:", filename)
            urlretrieve(url, target)
        # Controleer dat een download daadwerkelijk een (Big)TIFF is,
        # en bijvoorbeeld geen HTML-foutpagina van een proxy.
        with target.open("rb") as fh:
            signature = fh.read(4)
        if signature not in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"):
            raise RuntimeError(
                f"Ongeldig PROJ-gridbestand: {target}. "
                "Verwijder het bestand en start opnieuw om het te downloaden."
            )

    # Maak één expliciete PROJ_DATA map. Deze bevat naast de drie grids
    # ook proj.db; daardoor is het een volwaardige, geldige PROJ gegevensmap.
    original_dirs = datadir.get_data_dir().split(os.pathsep)
    source_dir = next(
        (Path(part) for part in original_dirs if (Path(part) / "proj.db").is_file()),
        None,
    )
    if source_dir is None:
        raise RuntimeError("proj.db ontbreekt in de pyproj-installatie.")

    combined_dir = user_dir / "zeeland_glb_proj_data"
    combined_dir.mkdir(parents=True, exist_ok=True)
    for name in ("proj.db", "proj.ini"):
        source = source_dir / name
        if source.is_file():
            shutil.copy2(source, combined_dir / name)
    for filename in REQUIRED_PROJ_GRIDS:
        shutil.copy2(user_dir / filename, combined_dir / filename)

    datadir.set_data_dir(str(combined_dir))
    print("Actieve PROJ-map:", datadir.get_data_dir())
    group = TransformerGroup("EPSG:7415", "EPSG:4978", always_xy=True, allow_ballpark=False)
    if not group.best_available or not group.transformers:
        missing = sorted({
            grid.short_name
            for operation in group.unavailable_operations
            for grid in operation.grids
            if not grid.available
        })
        raise RuntimeError(
            "Exacte RD/NAP -> ECEF conversie ontbreekt, ondanks de PROJ-grids. "
            + (f"Niet beschikbaar: {', '.join(missing)}" if missing else "Controleer pyproj/PROJ.")
        )

    transformer = Transformer.from_crs(
        "EPSG:7415", "EPSG:4978", always_xy=True,
        allow_ballpark=False, only_best=True,
    )
    print("Exacte coördinatentransformatie beschikbaar.")
    return transformer


def patch_glb_double_sided(glb_path: Path, obj_id: str, metadata: dict):
    """Zorg dat beide gevelzijden zichtbaar zijn (v7-regel); metadata per model."""
    blob = glb_path.read_bytes()
    if len(blob) < 20:
        raise ValueError(f"Ongeldig GLB: {glb_path}")
    magic, version, length = struct.unpack_from("<4sII", blob, 0)
    if magic != b"glTF" or version != 2 or length != len(blob):
        raise ValueError(f"Ongeldig GLB v2: {glb_path}")
    pos = 12
    chunks = []
    found_json = False
    while pos < len(blob):
        chunk_len, chunk_type = struct.unpack_from("<II", blob, pos)
        pos += 8
        data = blob[pos:pos + chunk_len]
        pos += chunk_len
        if chunk_type == 0x4E4F534A:  # JSON
            gltf = json.loads(data.decode("utf-8"))
            materials = gltf.setdefault("materials", [])
            if not materials:
                materials.append({
                    "doubleSided": True,
                    "pbrMetallicRoughness": {"metallicFactor": 0.0, "roughnessFactor": 1.0},
                })
                for mesh in gltf.get("meshes", []):
                    for primitive in mesh.get("primitives", []):
                        primitive["material"] = 0
            for material in materials:
                material["doubleSided"] = True
            asset_extras = gltf.setdefault("asset", {}).setdefault("extras", {})
            asset_extras["bag_object_id"] = obj_id
            # glTF ondersteunt vrije metadata in extras, zonder speciale extensie.
            # Zowel asset.extras als node.extras (eerste scene-node) blijven geldig.
            asset_extras["gebouw"] = json_safe(metadata)
            nodes = gltf.get("nodes", [])
            if nodes:
                nodes[0].setdefault("extras", {})["gebouw"] = json_safe(metadata)
            data = json.dumps(gltf, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            data += b" " * ((-len(data)) % 4)
            found_json = True
        chunks.append((chunk_type, data))
    if not found_json:
        raise ValueError("GLB heeft geen JSON chunk")
    body = b"".join(struct.pack("<II", len(data), ctype) + data for ctype, data in chunks)
    glb_path.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body)


def export_one_building(
    obj_id: str,
    obj: dict,
    city_objects: dict,
    rd_vertices: np.ndarray,
    outpath: Path,
    transformer,
    source_file: str,
):
    """Schrijf één Building (incl. BuildingParts) naar een zelfstandige Y-up GLB."""
    try:
        import mapbox_earcut as earcut  # v7-afhankelijkheid
    except ImportError as exc:
        raise RuntimeError("Installatie vereist: python -m pip install mapbox-earcut") from exc
    # Triangulatie blijft 100% volgens v7, inclusief LoD2.2/earcut.
    faces = []
    for part in get_building_geometries(obj_id, obj, city_objects):
        geom = choose_geometry(part)
        if geom:
            for surface in iter_cityjson_surfaces_from_geometry(geom):
                faces.extend(triangulate_surface_earcut(surface, rd_vertices))
    if not faces:
        return None

    used_ids = np.unique(np.asarray(faces, dtype=np.int64))
    if used_ids.min() < 0 or used_ids.max() >= len(rd_vertices):
        raise ValueError(f"Ongeldige vertex-indexen voor {obj_id}")
    rd_used = rd_vertices[used_ids]
    # Pivot aan de onderkant, midden van het gebouw: geschikt voor later verplaatsen.
    rd_anchor = np.array([
        (rd_used[:, 0].min() + rd_used[:, 0].max()) / 2.0,
        (rd_used[:, 1].min() + rd_used[:, 1].max()) / 2.0,
        float(rd_used[:, 2].min()),
    ], dtype=np.float64)
    ox, oy, oz = transformer.transform(*rd_anchor)
    origin_ecef = np.array([ox, oy, oz], dtype=np.float64)
    x, y, z = transformer.transform(rd_used[:, 0], rd_used[:, 1], rd_used[:, 2])
    ecef_used = np.column_stack((x, y, z))
    if not np.isfinite(ecef_used).all() or not np.isfinite(origin_ecef).all():
        raise RuntimeError("RD/NAP naar ECEF geeft ongeldige waarden. Controleer PROJ grids.")
    local, east, north, up = ecef_to_local_enu(ecef_used, origin_ecef)
    # v7 gebruikt intern Z-up ENU. Losse glTF/GLB gebruiken Y-up:
    # GLB x=east, y=up, z=-north (rechtshandig coördinatensysteem).
    local_y_up = np.column_stack((local[:, 0], local[:, 2], -local[:, 1]))
    index_map = {int(old): i for i, old in enumerate(used_ids)}
    verts, triangles, colors = [], [], []
    for a, b, c in faces:
        v0, v1, v2 = (local_y_up[index_map[int(i)]] for i in (a, b, c))
        normal = np.cross(v1 - v0, v2 - v0)
        if np.linalg.norm(normal) < 1e-9:
            continue
        # Terug naar ENU/Z-up voor exact dezelfde grijsschakering als v7.
        enu_normal = np.array([normal[0], -normal[2], normal[1]])
        enu_normal = enu_normal / np.linalg.norm(enu_normal)
        color = styled_grey_from_normal(enu_normal)
        start = len(verts)
        verts.extend((v0, v1, v2))
        triangles.append((start, start + 1, start + 2))
        colors.extend((color, color, color))
    if not triangles:
        return None
    vertices = np.asarray(verts, dtype=np.float32)
    mesh = trimesh.Trimesh(
        vertices=vertices,
        faces=np.asarray(triangles, dtype=np.int64),
        process=False,
    )
    mesh.visual = trimesh.visual.ColorVisuals(mesh, vertex_colors=np.asarray(colors, dtype=np.uint8))
    outpath.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(outpath)

    lon, lat, h = ECEF_TO_LONLAT.transform(*origin_ecef)
    dims = vertices.max(axis=0) - vertices.min(axis=0)
    result = {
        "bag_id": str(obj.get("attributes", {}).get("identificatie") or obj_id),
        "city_object_id": obj_id,
        "bestand": outpath.name,
        "bron_cityjson": source_file,
        "rd_nap_origin": [float(v) for v in rd_anchor],
        "ecef_origin": [float(v) for v in origin_ecef],
        "wgs84_origin": {"longitude": float(lon), "latitude": float(lat), "ellipsoidehoogte_m": float(h)},
        "model_assen": "GLB Y-up: X=East, Y=Up, Z=South; lokaal in meters",
        "afmetingen_m": {"oost_west": float(dims[0]), "hoogte": float(dims[1]), "noord_zuid": float(dims[2])},
        "eigenschappen": feature_properties(obj_id, obj),
        "bron_attributen": json_safe(obj.get("attributes", {}) or {}),
    }
    # Bewaar dezelfde gegevens ook in de GLB, in een standaard glTF extras veld.
    patch_glb_double_sided(outpath, obj_id, result)
    return result


def within_scope(obj_id, obj, city_objects, rd_vertices, bbox, boundary):
    center = building_centroid_rd_from_geometries(obj_id, obj, city_objects, rd_vertices)
    if center is None:
        return False
    xmin, ymin, xmax, ymax = bbox
    if not (xmin <= center.x <= xmax and ymin <= center.y <= ymax):
        return False
    return boundary is None or boundary.covers(center)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Export van 3DBAG Zeeland naar losse GLB per BAG-pand")
    parser.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR,
                        help="Uitvoermap (standaard 3dbag_output naast dit script)")
    parser.add_argument("--input-dir", type=Path, help="Eigen map met bestaande CityJSON-bestanden")
    parser.add_argument("--boundary", type=Path, default=DEFAULT_BOUNDARY,
                        help="Provinciegrens.gpkg voor exacte provinciegrensfilter")
    parser.add_argument("--skip-download", action="store_true", help="Gebruik lokale CityJSON in plaats van download")
    parser.add_argument("--limit", type=int, default=0, help="Verwerk alleen de eerste N CityJSON-tegels")
    parser.add_argument("--max-buildings", type=int, default=0, help="Stop na N GLB-bestanden")
    parser.add_argument("--bag-id", action="append", help="Exporteer alleen dit BAG-ID; kan meermaals")
    parser.add_argument("--bbox", nargs=4, type=float, default=ZEELAND_BBOX_RD, metavar=("XMIN", "YMIN", "XMAX", "YMAX"),
                        help="Selectiegebied in RD-coördinaten")
    args = parser.parse_args(argv)
    if args.limit < 0 or args.max_buildings < 0:
        parser.error("--limit en --max-buildings moeten 0 of positief zijn")
    if args.input_dir and not args.skip_download:
        parser.error("--input-dir wordt gebruikt met --skip-download")
    try:
        import mapbox_earcut  # noqa: F401
    except ImportError as exc:
        parser.error("Ontbrekende package mapbox-earcut. Installeer via python -m pip install mapbox-earcut")

    outdir = args.outdir.resolve()
    glb_dir = outdir / "gebouwen"
    glb_dir.mkdir(parents=True, exist_ok=True)
    cityjson_dir = (args.input_dir or (outdir / "cityjson")).resolve()
    if args.skip_download:
        files = sorted(cityjson_dir.rglob("*.json")) if cityjson_dir.exists() else []
    else:
        files = download_cityjson_tiles(outdir, tuple(args.bbox), args.limit)
    if args.limit:
        files = files[:args.limit]
    if not files:
        parser.error(f"Geen CityJSON-bestanden gevonden in {cityjson_dir}. Download eerst of controleer --input-dir")

    boundary = load_boundary(args.boundary)
    transformer = ensure_exact_transformer()
    wanted = {normalize_bag_id(v) for v in args.bag_id} if args.bag_id else None
    found = set()
    seen = set()
    output_count = 0
    skip_count = 0
    index_path = outdir / "gebouwen_index.jsonl"
    manifest_path = glb_dir / "manifest.json"
    # JSON en JSONL streamen: heel Zeeland kan veel BAG-panden bevatten.
    # manifest.json kan Ricardo rechtstreeks als modellenbibliotheek gebruiken.
    with (index_path.open("w", encoding="utf-8") as index,
          manifest_path.open("w", encoding="utf-8") as manifest):
        manifest.write(json.dumps({"schema_version": 1, "models_base_url": "/models/", "property_labels": {k: v[0] for k, v in PROPERTY_DEFS.items()}}, ensure_ascii=False)[:-1])
        manifest.write(', "buildings": [\n')
        first_manifest_entry = True
        for file_i, cj_path in enumerate(files, 1):
            print(f"[{file_i}/{len(files)}] {cj_path.name}")
            with cj_path.open("r", encoding="utf-8") as source:
                cityjson = json.load(source)
            rd_vertices = apply_cityjson_transform(cityjson)
            city_objects = cityjson.get("CityObjects", {})
            for obj_id, obj in city_objects.items():
                if obj.get("type") != "Building":
                    continue
                if obj_id in seen:
                    continue
                if not matches_bag_id(obj_id, obj, wanted):
                    continue
                if not within_scope(obj_id, obj, city_objects, rd_vertices, args.bbox, boundary):
                    skip_count += 1
                    continue
                filename = safe_filename(obj_id) + ".glb"
                outpath = glb_dir / filename
                result = export_one_building(
                    obj_id, obj, city_objects, rd_vertices, outpath, transformer, cj_path.name
                )
                seen.add(obj_id)
                if result is None:
                    skip_count += 1
                    continue
                index.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + "\n")
                manifest_entry = {
                    "id": result["city_object_id"],
                    "bag_id": result["bag_id"],
                    "name": f"Pand {result['bag_id']}",
                    "modelUrl": f"/models/{result['bestand']}",
                    "bestand": result["bestand"],
                    "eigenschappen": result["eigenschappen"],
                    "bron_attributen": result["bron_attributen"],
                    "wgs84_origin": result["wgs84_origin"],
                    "afmetingen_m": result["afmetingen_m"],
                    "bron_cityjson": result["bron_cityjson"],
                }
                if not first_manifest_entry:
                    manifest.write(',\n')
                manifest.write(json.dumps(manifest_entry, ensure_ascii=False, allow_nan=False))
                first_manifest_entry = False
                output_count += 1
                if wanted is not None:
                    found.update(v for v in wanted if matches_bag_id(obj_id, obj, {v}))
                if args.max_buildings and output_count >= args.max_buildings:
                    break
                if wanted is not None and found == wanted:
                    break
            if (args.max_buildings and output_count >= args.max_buildings) or (wanted is not None and found == wanted):
                break
        manifest.write('\n]}\n')
    print(f"Klaar: {output_count} GLB-bestanden in {glb_dir}")
    print(f"Modellenbibliotheek voor LEIA: {manifest_path}")
    print(f"Gebouwlocaties en eigenschappen: {index_path}")
    print(f"Overgeslagen (buiten selectie of zonder geometrie): {skip_count}")
    if wanted is not None and found != wanted:
        print("Niet gevonden BAG-ID(s):", ", ".join(sorted(wanted - found)))
    if output_count == 0:
        raise RuntimeError("Geen GLB-bestanden gemaakt: controleer BAG-ID, bbox, provinciegrens en CityJSON")
    return output_count


if __name__ == "__main__":
    main()
