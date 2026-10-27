"""Wind design choices, distinct from resource bins and ATB projection scenarios."""

DESIGNS = {
    "T1": dict(rated_kw=6000, rotor_m=170, hub_m=115, atb_class=7),
    "T2": dict(rated_kw=8300, rotor_m=196, hub_m=130, atb_class=8),
    "T3": dict(rated_kw=3300, rotor_m=148, hub_m=100, atb_class=9),
    "T4": dict(rated_kw=6000, rotor_m=196, hub_m=140, atb_class=10),
}
WIND_GROUP = "WIND_RESOURCE"
MULTI_TECHS = {f"WIND_GEN_{t}" for t in DESIGNS}


def model_name(design):
    specs = DESIGNS[design]
    return f"2023NREL_Bespoke_{specs['rated_kw'] / 1000:g}MW_{specs['rotor_m']}"


def package_identity(settings):
    from importlib.metadata import version
    from turbine_models import Turbines
    from geocanoe.acquisition.era5_renewables import sha256

    library = Turbines()
    selected = (
        ["T1"]
        if mode(settings) == "t1_only"
        else ["T1", "T2", "T3"]
        if mode(settings) == "selectable_t1_t2_t3"
        else DESIGNS
    )
    return {
        "package": "turbine-models",
        "version": version("turbine-models"),
        "files": {
            design: {
                "model": model_name(design),
                "curve_sha256": sha256(
                    library.paths["onshore"] / f"{model_name(design)}.csv"
                ),
                "specs_sha256": sha256(
                    library.spec_paths["onshore"] / f"{model_name(design)}.yaml"
                ),
            }
            for design in selected
        },
    }


def mode(settings):
    selected = settings.get("technology", {}).get("wind_representation", "t1_only")
    if selected not in {"t1_only", "selectable_t1_t2_t3", "resource_assigned"}:
        raise ValueError("Unsupported wind_representation.")
    return selected


def profile_field(tech):
    if tech in MULTI_TECHS:
        return "wind_cf_" + tech.rsplit("_", 1)[1].lower()
    return "wind_cf" if tech == "WIND_GEN" else "solar_cf"
