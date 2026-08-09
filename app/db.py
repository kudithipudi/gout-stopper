import json
import logging
from pathlib import Path

import aiosqlite

from app.config import get_settings

logger = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "schema.sql"

# Pre-seeded baseline list (editable via the admin UI). Aliases are comma-
# separated lowercase forms used for fuzzy matching against LLM-detected items.
DEFAULT_FOODS: list[tuple[str, str, str, str]] = [
    # (name, category, aliases, notes)
    ("beer", "avoid", "beers, lager, ale, stout, pilsner",
     "Alcohol raises uric acid; beer is worst for gout."),
    ("wine", "limit", "red wine, white wine",
     "Alcohol in moderation; spirits are less purine-heavy than beer."),
    ("hard liquor", "limit", "spirits, whiskey, vodka, rum, gin",
     "Alcohol raises uric acid; keep to occasional."),
    ("organ meats", "avoid", "liver, kidney, sweetbreads, heart, tripe, pate",
     "Very high in purines."),
    ("red meat", "avoid", "beef, lamb, pork, steak, veal, venison, hamburger, beef patty, ribs, bacon, sausage, hot dog, pepperoni",
     "High purine; limit servings."),
    ("game meat", "avoid", "venison, rabbit, duck, goose, boar",
     "High purine."),
    ("anchovies", "avoid", "anchovy, anchovy paste",
     "Very high purine fish."),
    ("sardines", "avoid", "sardine",
     "Very high purine fish."),
    ("mackerel", "avoid", "mackerel",
     "High purine fish."),
    ("herring", "avoid", "herring",
     "Very high purine fish."),
    ("mussels", "avoid", "mussel",
     "High purine shellfish."),
    ("scallops", "avoid", "scallop",
     "High purine shellfish."),
    ("shrimp", "avoid", "prawns, prawn, shrimp, gambas",
     "High purine shellfish."),
    ("lobster", "avoid", "lobster, langoustine",
     "High purine shellfish."),
    ("crab", "limit", "crab meat",
     "Moderate purine shellfish."),
    ("oysters", "limit", "oyster",
     "Moderate-high purine shellfish."),
    ("clam", "limit", "clams, cockles",
     "Moderate purine shellfish."),
    ("salmon", "limit", "salmon",
     "Moderate purine; omega-3s are beneficial."),
    ("tuna", "limit", "tuna, ahi",
     "Moderate purine fish."),
    ("trout", "limit", "trout",
     "Moderate purine fish."),
    ("cod", "limit", "cod, hake, haddock",
     "Moderate purine white fish."),
    ("tilapia", "limit", "tilapia",
     "Moderate purine fish."),
    ("chicken", "limit", "poultry, chicken breast, wings",
     "Moderate purine; skinless breast is a better pick."),
    ("turkey", "limit", "turkey",
     "Moderate purine."),
    ("lentils", "limit", "lentil, dal, dal soup",
     "Moderate purine legumes."),
    ("chickpeas", "limit", "chickpea, garbanzo, hummus",
     "Moderate purine legumes."),
    ("dried beans", "limit", "beans, kidney beans, black beans, pinto beans, navy beans",
     "Moderate purine legumes."),
    ("soy", "limit", "tofu, edamame, soybeans",
     "Moderate purine; studies are mixed, consume in moderation."),
    ("mushrooms", "limit", "mushroom, portobello",
     "Moderate purine vegetables."),
    ("asparagus", "limit", "asparagus",
     "Moderate purine vegetable; generally fine in normal portions."),
    ("cauliflower", "limit", "cauliflower",
     "Moderate purine vegetable; generally fine in normal portions."),
    ("spinach", "ok", "spinach",
     "Low purine despite old guidance; fine."),
    ("oats", "limit", "oatmeal, oatmeal porridge",
     "Moderate purine grain."),
    ("sugary drinks", "avoid", "soda, pop, cola, soft drink, energy drink, sweet tea",
     "Fructose raises uric acid; avoid sugar-sweetened drinks."),
    ("fruit juice", "limit", "orange juice, apple juice, fruit juice",
     "High-fructose fruit juice can raise uric acid."),
    ("sweetbreads", "avoid", "sweetbread, thymus",
     "Very high purine organ meat."),
    ("gravy", "avoid", "gravy, au jus, meat sauce",
     "Concentrated meat extract is high purine."),
    ("broth", "avoid", "bone broth, beef broth, chicken stock, bouillon",
     "Concentrated meat extracts are high purine."),
    ("yeast extract", "avoid", "marmite, vegemite, nutritional yeast",
     "Very high purine."),
    ("mayonnaise", "ok", "mayo, aioli",
     "Fine for gout."),
    ("eggs", "ok", "egg, eggs",
     "Low purine, good protein choice."),
    ("low-fat dairy", "ok", "milk, yogurt, cheese, greek yogurt",
     "Low-fat dairy may lower uric acid."),
    ("nuts", "ok", "almonds, walnuts, peanuts, cashews, pecans, pistachios",
     "Low purine."),
    ("rice", "ok", "rice, brown rice, white rice",
     "Low purine grain."),
    ("pasta", "ok", "pasta, noodles, spaghetti",
     "Low purine grain."),
    ("bread", "ok", "bread, toast, bagel, sandwich",
     "Low purine."),
    ("potatoes", "ok", "potato, french fries, fries, chips, baked potato",
     "Low purine (fries are high calorie though)."),
    ("corn", "ok", "corn, sweetcorn, popcorn",
     "Low purine."),
    ("cherries", "ok", "cherries, tart cherry",
     "May lower uric acid."),
    ("berries", "ok", "blueberries, strawberries, raspberries",
     "Low purine."),
    ("citrus", "ok", "oranges, lemons, grapefruit, oranges",
     "Vitamin C may help lower uric acid."),
    ("apples", "ok", "apple",
     "Low purine."),
    ("bananas", "ok", "banana",
     "Low purine."),
    ("leafy greens", "ok", "lettuce, kale, cabbage, arugula, salad",
     "Low purine."),
    ("tomatoes", "ok", "tomato, tomatoes",
     "Low purine."),
    ("cucumbers", "ok", "cucumber",
     "Low purine."),
    ("water", "ok", "water",
     "Staying hydrated helps flush uric acid."),
    ("coffee", "ok", "coffee, espresso, latte",
     "May modestly lower uric acid."),
    ("tea", "ok", "tea, black tea, green tea",
     "Low purine."),
    ("chocolate", "ok", "chocolate, cocoa, dark chocolate",
     "Low purine."),
    ("olive oil", "ok", "olive oil",
     "Fine for gout."),
]


async def connect(db_path: str | None = None) -> aiosqlite.Connection:
    db_path = db_path or get_settings().db_path
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    return conn


async def init_db(db_path: str | None = None) -> None:
    conn = await connect(db_path)
    try:
        schema = _SCHEMA_PATH.read_text()
        await conn.executescript(schema)
        await _migrate(conn)
        await _seed_foods(conn)
        await conn.commit()
        logger.info("Database schema applied")
    finally:
        await conn.close()


async def _migrate(conn: aiosqlite.Connection) -> None:
    """Additive migrations for databases created before a column existed.
    CREATE TABLE IF NOT EXISTS never touches existing tables, so schema
    additions need an explicit ALTER here."""
    cols = {
        row["name"]
        for row in await conn.execute_fetchall("PRAGMA table_info(scans)")
    }
    if "query_text" not in cols:
        await conn.execute("ALTER TABLE scans ADD COLUMN query_text TEXT")
        logger.info("Migration: added scans.query_text column")


async def _seed_foods(conn: aiosqlite.Connection) -> None:
    """Populate the baseline list once, only if the foods table is empty."""
    count = (await conn.execute_fetchall("SELECT COUNT(*) AS n FROM foods"))[0]["n"]
    if count > 0:
        return
    await conn.executemany(
        "INSERT INTO foods (name, category, aliases, notes) VALUES (?, ?, ?, ?)",
        DEFAULT_FOODS,
    )
    logger.info("Seeded %d baseline foods", len(DEFAULT_FOODS))


async def get_db():
    conn = await connect()
    try:
        yield conn
    finally:
        await conn.close()


async def get_scan(conn: aiosqlite.Connection, scan_id: int) -> dict | None:
    row = await conn.execute_fetchall("SELECT * FROM scans WHERE id = ?", (scan_id,))
    if not row:
        return None
    scan = dict(row[0])
    scan["detected_items"] = json.loads(scan.get("detected_items") or "[]")
    scan["matched_foods"] = json.loads(scan.get("matched_foods") or "[]")
    return scan
