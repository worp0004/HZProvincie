"""
Maakt een snellere versie van de 3DBAG Zeeland tileset.json voor LEIA.

De tileset van de provincie heeft geen detailniveaus: alle tegels hangen direct onder de root.
Cesium laadt dan elke tegel die in beeld is, tot aan de horizon (bij heel Zeeland in beeld ongeveer 1 GB).
Dit script zet elke tegel in een eigen tussenlaag met een geometricError,
zodat Cesium een tegel pas laadt als de camera binnen een bepaalde afstand komt.
De b3dm-bestanden zelf blijven op de server van de provincie.

Voorbeeld:
python scripts/maak_tileset_lod.py
"""

import json
from pathlib import Path
from urllib.request import urlopen

SOURCE_URL = "https://virtueel.zeeland.nl/tiles_other/bagnl/tileset.json"
OUTFILE = Path(__file__).resolve().parents[1] / "static" / "tilesets" / "zeeland-3dbag" / "tileset.json"

# Via de proxy in vite.config.ts, op de server van de provincie is dit hetzelfde domein
CONTENT_PATH = "/tiles_other/bagnl/"

# Hoe groter, hoe verder weg tegels al laden. 200 laadt tegels tot ongeveer 10 km van de camera,
# zodat je bij ronddraaien alles ziet maar bij uitzoomen niet heel Zeeland (ongeveer 1 GB) laadt.
GEOMETRIC_ERROR = 200


def wrap_tile(tile):
    # De tussenlaag krijgt de positie en omvang van de tegel, de tegel zelf alleen nog de inhoud
    content_tile = {
        "boundingVolume": tile["boundingVolume"],
        "geometricError": 0,
        "content": {"uri": CONTENT_PATH + tile["content"]["uri"]},
    }
    return {
        "boundingVolume": tile["boundingVolume"],
        "geometricError": GEOMETRIC_ERROR,
        "transform": tile["transform"],
        "refine": "ADD",
        "children": [content_tile],
    }


def main():
    with urlopen(SOURCE_URL) as response:
        tileset = json.load(response)

    root = tileset["root"]
    root["children"] = [wrap_tile(tile) for tile in root["children"]]

    OUTFILE.parent.mkdir(parents=True, exist_ok=True)
    OUTFILE.write_text(json.dumps(tileset), encoding="utf-8")
    print(f"{len(root['children'])} tegels, opgeslagen in {OUTFILE}")


if __name__ == "__main__":
    main()
