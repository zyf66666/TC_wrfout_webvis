"""Cross-domain theta-e anomalies from cached layer-centred environment fields."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from wrf_webvis.app.core.paths import PathManager, atomic_write_json, variable_metadata_path
from wrf_webvis.app.plotting.polar_anomaly import environmental_anomaly
from .processing_utils import atomic_save


def _fingerprint(path: Path) -> dict:
    stat = path.stat()
    return {"path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def process_theta_e_anomaly(request, time_hour, heights) -> list[str]:
    """Read cached inputs only; never generate or overwrite either source field."""
    paths = PathManager(request.processed_root)
    env_domain = int(request.options.get("theta_e_environment_domain", 2))
    ring = list(request.options.get("theta_e_environment_ring_km", [400.0, 500.0]))
    if env_domain == request.domain:
        raise ValueError("theta_e_anomaly: environment domain must differ from target domain")
    if len(ring) != 2 or not np.isfinite(ring).all() or not 0 <= ring[0] < ring[1]:
        raise ValueError("theta_e_environment_ring_km must contain two increasing finite radii")
    outputs = []
    for height in heights:
        common = dict(member_name=request.member_name, time_hour=time_hour, height_m=height, dimension="3d")
        source = paths.variable_path(**common, domain=request.domain, variable="theta_e")
        reference = paths.variable_path(**common, domain=env_domain, variable="theta_e1_polar")
        for path in (source, reference):
            if not path.exists():
                raise FileNotFoundError(f"theta_e_anomaly requires existing source: {path}")
        per_file = reference.with_suffix(".npy.json")
        meta_path = per_file if per_file.exists() else variable_metadata_path(reference)
        if not meta_path.exists():
            raise FileNotFoundError(f"Missing radius/centre metadata for {reference}: {meta_path}")
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        if metadata.get("center_mode") != "layer":
            raise ValueError(f"Environment must use layer centres: {meta_path}")
        resolution = float(metadata.get("radius_resolution_km", float("nan")))
        if not np.isfinite(resolution) or resolution <= 0:
            raise ValueError(f"Missing valid radius_resolution_km: {meta_path}")
        output = paths.variable_path(**common, domain=request.domain, variable="theta_e_anomaly")
        record_path = output.with_suffix(".npy.environment.json")
        signature = dict(algorithm="cross_domain_layer_ring_v1", environment_domain=env_domain,
                         ring_km=ring, radius_resolution_km=resolution, dtype=request.dtype,
                         source=_fingerprint(source), environment_source=_fingerprint(reference),
                         metadata=_fingerprint(meta_path))
        if output.exists() and record_path.exists() and not request.force:
            record = json.loads(record_path.read_text(encoding="utf-8"))
            if record.get("signature") == signature and record.get("output") == _fingerprint(output):
                continue
        ref = np.load(reference)
        if ref.ndim != 2 or ref.shape[0] != 360:
            raise ValueError(f"Expected 360 azimuths in layer-centred reference: {reference}, {ref.shape}")
        radii = np.arange(ref.shape[1], dtype=float) * resolution
        _, environment, coverage = environmental_anomaly(ref, radii, ring)
        field = np.load(source)
        if field.ndim != 2:
            raise ValueError(f"Expected per-height Cartesian theta_e: {source}, {field.shape}")
        anomaly = np.where(np.isfinite(field), field.astype(np.float64) - environment, np.nan)
        atomic_save(output, anomaly.astype(request.dtype))
        record = dict(signature=signature, output=_fingerprint(output),
                      environment_domain=env_domain, environment_variable="theta_e1_polar",
                      ring_km=ring, radius_resolution_km=resolution, center_mode="layer",
                      environment_theta_e_K=environment, valid_fraction=coverage,
                      weighting="radius; finite samples only; ring endpoints included",
                      member=request.member_name, time_h=time_hour, height_m=height, units="K",
                      definition="target theta_e minus same-time same-height environment mean")
        atomic_write_json(record_path, record)
        paths.write_metadata(output, {**record, "variable": "theta_e_anomaly"})
        outputs.append(str(output))
    return outputs
