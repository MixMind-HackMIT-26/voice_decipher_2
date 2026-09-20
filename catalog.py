"""The deployed bottle order and proposal policy, shared with the experiment."""
import math

CATALOG = {
    1: {"name": "Orange juice", "profile": "sweet citrus base", "max_ml": 60},
    2: {"name": "Cranberry", "profile": "tart red fruit; bottle-dependent sweetness", "max_ml": 60},
    3: {"name": "Lime cordial", "profile": "concentrated sweet-sour lime accent", "max_ml": 20},
    4: {"name": "Ginger ale", "profile": "sweet sparkling ginger mixer", "max_ml": 60},
    5: {"name": "Grape juice", "profile": "sweet dark fruit", "max_ml": 60},
    6: {"name": "Apple juice", "profile": "sweet mellow fruit", "max_ml": 60},
}
INGREDIENTS = {str(ch): item["name"] for ch, item in CATALOG.items()}
SAMPLE_RATIO = 0.08


def validate(recipe, excluded=()):
    pours = recipe.get("pours", [])
    if not 2 <= len(pours) <= 6:
        raise ValueError("A recipe needs 2-6 ingredients")
    seen = set()
    for p in pours:
        ch, ml = p["channel"], p["ml"]
        if type(ch) is not int or ch not in CATALOG or ch in seen:
            raise ValueError("Invalid or duplicate channel")
        if ch in excluded:
            raise ValueError("Excluded ingredient: " + CATALOG[ch]["name"])
        if type(ml) not in (int, float) or not math.isfinite(ml) or not 10 <= ml <= CATALOG[ch]["max_ml"]:
            raise ValueError("Ingredient dose outside deployed limits")
        seen.add(ch)
    if sum(p["ml"] for p in pours) > 130:
        raise ValueError("Maximum final serving is 130 ml")
    if not isinstance(recipe.get("name"), str) or not 1 <= len(recipe["name"].strip()) <= 80:
        raise ValueError("Drink name must be 1-80 characters")


def sample_recipe(recipe):
    validate(recipe)
    pours = [{"channel": p["channel"], "ml": round(p["ml"] * SAMPLE_RATIO, 4)}
             for p in recipe["pours"]]
    return {"name": recipe["name"], "pours": pours, "stir_seconds": 0,
            "ml_total": round(sum(p["ml"] for p in pours), 4)}
