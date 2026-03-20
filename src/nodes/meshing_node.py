from services.mesh import copy_custom_mesh, prepare_standard_mesh, handle_gmsh_mesh as service_handle_gmsh_mesh
from services.stl_utils import find_stl_files, copy_stls_to_trisurface, build_stl_context
import os


def _detect_stl_files(state):
    """Detect STL files from state['stl_dir'] or project-level directories."""
    stl_paths = []

    # 1. Explicit stl_dir from CLI
    stl_dir = state.get("stl_dir")
    if stl_dir and os.path.exists(stl_dir):
        stl_paths = find_stl_files(stl_dir)
        if stl_paths:
            print(f"Found {len(stl_paths)} STL files from --stl_dir: {stl_dir}")
            return stl_paths

    # 2. Check if custom_mesh_path points to an STL file or directory of STLs
    custom_path = state.get("custom_mesh_path")
    if custom_path and os.path.exists(custom_path):
        if custom_path.lower().endswith(".stl"):
            print(f"Found STL from --custom_mesh_path: {custom_path}")
            return [custom_path]
        if os.path.isdir(custom_path):
            stl_paths = find_stl_files(custom_path)
            if stl_paths:
                print(f"Found {len(stl_paths)} STL files from --custom_mesh_path dir: {custom_path}")
                return stl_paths

    return stl_paths


def handle_standard_mesh(state, case_dir):
    """Handle standard OpenFOAM mesh generation.

    If STL files are detected (from --stl_dir or --custom_mesh_path),
    copy them to constant/triSurface/ and compute geometry metadata
    so that input_writer can generate accurate OpenFOAM dicts.
    """
    print("============================== Standard Mesh Generation ==============================")

    stl_paths = _detect_stl_files(state)

    if stl_paths:
        print(f"Preparing {len(stl_paths)} STL files for snappyHexMesh workflow...")
        copy_stls_to_trisurface(stl_paths, case_dir)
        stl_context = build_stl_context(stl_paths)
        print(stl_context)
        return {
            "mesh_info": {"stl_files": [os.path.basename(p) for p in stl_paths]},
            "mesh_commands": [],
            "mesh_file_destination": None,
            "custom_mesh_used": False,
            "stl_context": stl_context,
            "error_logs": [],
        }

    print("Using standard OpenFOAM mesh generation (blockMesh, snappyHexMesh, etc.)")
    return {
        "mesh_info": None,
        "mesh_commands": [],
        "mesh_file_destination": None,
        "custom_mesh_used": False,
        "error_logs": []
    }


def meshing_node(state):
    """
    Meshing node: Handle different mesh scenarios based on user requirements.
    
    Three scenarios:
    1. Custom mesh: User provides existing mesh file (uses preprocessor logic)
    2. GMSH mesh: User wants mesh generated using GMSH (uses gmsh python logic)
    3. Standard mesh: User wants standard OpenFOAM mesh generation (returns None)
    
    Updates state with:
      - mesh_info: Information about the custom mesh
      - mesh_commands: Commands needed for mesh processing
      - mesh_file_destination: Where the mesh file should be placed
    """
    config = state["config"]
    user_requirement = state["user_requirement"]
    case_dir = state["case_dir"]
    
    # Get mesh type from state (determined by router)
    mesh_type = state.get("mesh_type", "standard_mesh")
    
    # Handle mesh based on type determined by router
    if mesh_type == "custom_mesh":
        print("Router determined: Custom mesh requested.")
        return copy_custom_mesh(state.get("custom_mesh_path"), user_requirement, case_dir)  # service
    elif mesh_type == "gmsh_mesh":
        print("Router determined: GMSH mesh requested.")
        return service_handle_gmsh_mesh(state, case_dir)  # service
    else:
        print("Router determined: Standard mesh generation.")
        return handle_standard_mesh(state, case_dir)
