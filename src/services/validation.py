"""Pre-flight validation for OpenFOAM cases.

Runs checks (and auto-fixes where safe) before Allrun execution to catch
common LLM-generated errors early rather than burning reviewer loop iterations.
"""

import os
import re
from typing import List, Tuple


# ---------------------------------------------------------------------------
# Default meshQualityDict content for snappyHexMesh
# ---------------------------------------------------------------------------
DEFAULT_MESH_QUALITY_DICT = """\
FoamFile
{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      meshQualityDict;
}

maxNonOrtho         65;
maxBoundarySkewness 20;
maxInternalSkewness 4;
maxConcave          80;
minVol              1e-13;
minTetQuality       1e-15;
minArea             -1;
minTwist            0.02;
minDeterminant      0.001;
minFaceWeight       0.05;
minVolRatio         0.01;
minTriangleTwist    -1;
nSmoothScale        4;
errorReduction      0.75;
"""


def preflight_check(case_dir: str) -> List[str]:
    """Run all pre-flight checks. Returns list of warnings (empty = all good)."""
    warnings: List[str] = []

    warnings.extend(_check_mesh_quality_dict(case_dir))
    warnings.extend(_check_surface_features_dict(case_dir))
    warnings.extend(_check_snappyhexmesh_stl_refs(case_dir))
    warnings.extend(_check_allrun_executable(case_dir))

    if warnings:
        print(f"Pre-flight: {len(warnings)} issue(s) found and auto-fixed where possible:")
        for w in warnings:
            print(f"  ⚠ {w}")
    else:
        print("Pre-flight: All checks passed.")

    return warnings


def _check_mesh_quality_dict(case_dir: str) -> List[str]:
    """If snappyHexMeshDict includes meshQualityDict, ensure it exists."""
    warnings = []
    shm_path = os.path.join(case_dir, "system", "snappyHexMeshDict")
    if not os.path.exists(shm_path):
        return warnings

    with open(shm_path, "r", errors="replace") as f:
        content = f.read()

    if "meshQualityDict" in content:
        mqd_path = os.path.join(case_dir, "system", "meshQualityDict")
        if not os.path.exists(mqd_path):
            with open(mqd_path, "w") as f:
                f.write(DEFAULT_MESH_QUALITY_DICT)
            warnings.append(f"AUTO-FIXED: Created missing {mqd_path}")

    return warnings


def _check_surface_features_dict(case_dir: str) -> List[str]:
    """Ensure surfaceFeatures dict file exists and matches what Allrun calls."""
    warnings = []
    allrun_path = os.path.join(case_dir, "Allrun")
    if not os.path.exists(allrun_path):
        return warnings

    with open(allrun_path, "r", errors="replace") as f:
        allrun_content = f.read()

    # surfaceFeatures command looks for system/surfaceFeaturesDict
    if "surfaceFeatures" in allrun_content:
        correct_path = os.path.join(case_dir, "system", "surfaceFeaturesDict")
        wrong_path = os.path.join(case_dir, "system", "surfaceFeatureExtractDict")

        if not os.path.exists(correct_path) and os.path.exists(wrong_path):
            os.rename(wrong_path, correct_path)
            warnings.append(f"AUTO-FIXED: Renamed surfaceFeatureExtractDict → surfaceFeaturesDict")

    return warnings


def _check_snappyhexmesh_stl_refs(case_dir: str) -> List[str]:
    """Check that STL files referenced in snappyHexMeshDict exist in constant/triSurface/."""
    warnings = []
    shm_path = os.path.join(case_dir, "system", "snappyHexMeshDict")
    if not os.path.exists(shm_path):
        return warnings

    with open(shm_path, "r", errors="replace") as f:
        content = f.read()

    # Find all .stl file references
    stl_refs = re.findall(r'["\s]([A-Za-z0-9_\-]+\.stl)', content)
    tri_dir = os.path.join(case_dir, "constant", "triSurface")

    for stl_name in set(stl_refs):
        stl_full = os.path.join(tri_dir, stl_name)
        if not os.path.exists(stl_full):
            # Check if it's referenced with a path prefix like constant/triSurface/
            # Also check if it exists anywhere in the case dir
            found = False
            for root, _, files in os.walk(case_dir):
                if stl_name in files:
                    src = os.path.join(root, stl_name)
                    os.makedirs(tri_dir, exist_ok=True)
                    import shutil
                    shutil.copy2(src, stl_full)
                    warnings.append(f"AUTO-FIXED: Copied {stl_name} to constant/triSurface/")
                    found = True
                    break
            if not found:
                warnings.append(f"WARNING: STL file {stl_name} referenced in snappyHexMeshDict but not found anywhere in case")

    return warnings


def _check_allrun_executable(case_dir: str) -> List[str]:
    """Ensure Allrun has execute permission."""
    warnings = []
    allrun_path = os.path.join(case_dir, "Allrun")
    if os.path.exists(allrun_path) and not os.access(allrun_path, os.X_OK):
        os.chmod(allrun_path, 0o755)
        warnings.append("AUTO-FIXED: Set execute permission on Allrun")
    return warnings
