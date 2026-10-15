"""El log de payloads muestra la forma del JSON sin volcar geometrías ni listas enteras."""

from agent_service.api.payload_log import _summarize


def test_resume_geometrias_listas_y_textos():
    payload = {
        "message": "x" * 400,
        "layersGeojson": [
            {"name": "predios", "features": [
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 2]}, "properties": {"id": i}}
                for i in range(10)
            ]},
        ],
    }

    out = _summarize(payload)

    assert out["message"].endswith("<… 400 caracteres>")
    features = out["layersGeojson"][0]["features"]
    assert len(features) == 4
    assert features[0]["geometry"] == {"type": "Point", "coordinates": "<coordenadas>"}
    assert features[-1] == "<… 7 más, 10 en total>"
    # El original no se toca
    assert payload["layersGeojson"][0]["features"][0]["geometry"]["coordinates"] == [1, 2]
