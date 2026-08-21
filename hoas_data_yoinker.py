import asyncio
import logging
from datetime import datetime
from pathlib import Path
import time
import aiohttp
import pandas as pd
from bs4 import BeautifulSoup
from geopy.geocoders import Nominatim


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

BASE_URL = "https://hoas.fi"
AREAS_URL = "https://hoas.fi/alueet/"
TEST_URL_AREA = "https://hoas.fi/alueet/kannelmaki/"
TEST_URL_PROPERTY = "https://hoas.fi/kohteet/kitarakuja-1/"
GEOCODE_CACHE_PATH = Path("geocode_cache.csv")
CONC_LIMIT = 3

# Fixed output schema. Order here is the order that ends up in the CSV.
BASE_COLUMNS = [
    "location",
    "energy_class",
    "condition",
    "renovation_year",
    "building_year",
    "type",
    "address",
    "rooms",
    "surface_area",
    "count",
    "rent",
    "rating",
]

AMENITY_COLUMNS = [
    "Internet: Trinet",
    "Remontoitu tai uusi",
    "Hissi",
    "Vesimaksu kulutuksen mukaan",
    "Internet: DNA",
    "Pesutupa",
    "Kerhotila",
    "Internet: Telia",
    "Sauna",
]

OUTPUT_COLUMNS = BASE_COLUMNS + AMENITY_COLUMNS


def safe_text(el, default=""):
    """Return el.text.strip() or a default if el is None."""
    if el is None:
        return default
    return el.text.strip()


def safe_find_text(soup, tag, **kwargs):
    default = kwargs.pop("default", "")
    return safe_text(soup.find(tag, **kwargs), default)


def strip_suffix(value, suffix):
    return value.removesuffix(suffix) if value else value


async def fetch(session, url, semaphore):
    """Fetch a URL's text content, respecting the concurrency limit."""
    async with semaphore:
        try:
            async with session.get(url) as response:
                if response.status != 200:
                    logger.warning(f"  ! Non-200 status ({response.status}) for {url}")
                    return None
                return await response.text()
        except aiohttp.ClientError as e:
            logger.error(f"  ! Request failed for {url}: {e}")
            return None


async def get_area_urls(session, semaphore, areas_url=AREAS_URL):
    """Gets all the area urls."""
    plain_text = await fetch(session, areas_url, semaphore)
    if plain_text is None:
        return []

    soup = BeautifulSoup(plain_text, "html.parser")

    links = soup.find_all("a", href=True)
    links = [link["href"] for link in links if "/alueet/" in link["href"]]
    links = [link for link in links if link != AREAS_URL]

    return list(set(links))


async def scrape_area(session, area_url, semaphore):
    logger.info(f"Scraping area: {area_url.removeprefix('https://hoas.fi/alueet')}")

    plain_text = await fetch(session, area_url, semaphore)
    if plain_text is None:
        return []

    soup = BeautifulSoup(plain_text, "html.parser")

    links = soup.find_all("a", href=True)
    links = [link["href"] for link in links if "/kohteet/" in link["href"]]
    links = [link for link in links if link != "https://hoas.fi/kohteet/"]

    return list(set(links))


# ---------------------------------------------------------------------------
# Property parsing
# ---------------------------------------------------------------------------


def parse_services(soup):
    """Return the raw list of service/amenity strings found on the page."""
    services_div = soup.find("div", class_="services_list")
    if not services_div:
        return []

    lines = services_div.text.strip().split("\n")
    lines = [line.strip() for line in lines if line.strip() != ""]

    # The first line is a header ("Palvelut" / "Varusteet" etc.) - only drop
    # it if it doesn't look like one of our known amenities, so we never
    # accidentally eat a real amenity because the header was absent.
    if lines and lines[0] not in AMENITY_COLUMNS:
        lines = lines[1:]

    return lines


def parse_basic_info(soup):
    """Return (energy_class, renovation_year, building_year), all safely defaulted."""
    basic_info = soup.find("div", class_="property-table w-100 col-12")
    basic_dict = {}
    if basic_info:
        for row in basic_info.find_all("div", class_="row"):
            key_el = row.find("div", class_="col-12 col-md-3")
            value_el = row.find("div", class_="col-12 col-md-9")
            if key_el is None or value_el is None:
                continue
            basic_dict[key_el.text.strip()] = value_el.text.strip()

    energialuokka = basic_dict.get("Energialuokka", "")
    perusparannusvuosi = basic_dict.get("Perusparannusvuosi", "")
    if perusparannusvuosi:
        perusparannusvuosi = perusparannusvuosi.split(", ")[-1]
    rakennusvuosi = basic_dict.get("Rakennusvuosi", "")
    if rakennusvuosi:
        rakennusvuosi = rakennusvuosi.split(", ")[-1]

    return energialuokka, perusparannusvuosi, rakennusvuosi


def parse_apartments(soup, shared_fields, property_url):
    """Return one row per individual apartment listing on the page."""
    rows = []

    apartment_box = soup.find(
        "div", class_="element-property-apartments-listing--content"
    )
    if not apartment_box:
        logger.warning(f"  ! No apartment listing found on {property_url}")
        return rows

    types = apartment_box.find_all("div", class_="single-container")
    for apt_type in types:
        type_name = safe_find_text(apt_type, "div", class_="type")
        single_type = apt_type.find_all("div", class_="element-block apartment-info")

        for single in single_type:
            address_n_rooms = safe_find_text(single, "div", class_="apartment-address")
            address = address_n_rooms.split(", ")[0] if address_n_rooms else ""
            rooms = address_n_rooms.split(", ")[-1] if address_n_rooms else ""

            surface_area = strip_suffix(
                safe_find_text(single, "div", class_="surface-area"), " m²"
            )
            count = strip_suffix(safe_find_text(single, "div", class_="count"), " kpl")
            rent = strip_suffix(safe_find_text(single, "div", class_="rent"), " €")
            if rent == 0:
                rent = None
                logger.warning(f"  ! Rent is 0 for {property_url} {address}")
            row = dict(shared_fields)
            row.update(
                {
                    "type": type_name,
                    "address": address,
                    "rooms": rooms,
                    "surface_area": surface_area,
                    "count": count,
                    "rent": rent,
                }
            )
            rows.append(row)

    return rows


async def scrape_property(session, property_url, semaphore):
    logger.info(
        f"Scraping property: {property_url.removeprefix('https://hoas.fi/kohteet')}"
    )

    plain_text = await fetch(session, property_url, semaphore)
    if plain_text is None:
        return []

    try:
        soup = BeautifulSoup(plain_text, "html.parser")

        services = parse_services(soup)
        energialuokka, perusparannusvuosi, rakennusvuosi = parse_basic_info(soup)

        condition_span = soup.find("span", string=lambda x: x and "Kohteen kunto:" in x)
        condition_text = safe_text(condition_span, "no condition")
        if condition_text and condition_text != "no condition":
            condition_text = condition_text.removeprefix("Kohteen kunto: ")

        location = safe_find_text(soup, "span", class_="location")
        rating = safe_find_text(soup, "span", class_="rating", default="no rating")
        if rating != "no rating":
            rating = rating.removesuffix("/5")

        shared_fields = {
            "location": location,
            "energy_class": energialuokka,
            "condition": condition_text,
            "renovation_year": perusparannusvuosi,
            "building_year": rakennusvuosi,
            "rating": rating,
        }
        # amenity flags, fixed schema - 0/1 per known amenity
        for amenity in AMENITY_COLUMNS:
            shared_fields[amenity] = 1 if amenity in services else 0

        rows = parse_apartments(soup, shared_fields, property_url)

        unknown = [s for s in services if s not in AMENITY_COLUMNS]
        if unknown:
            logger.warning(f"  ! Unrecognized amenities on {property_url}: {unknown}")

        return rows

    except Exception as e:
        logger.error(f"  ! Failed to parse {property_url}: {e}")
        return []


async def gather_progress(tasks, label):
    """Run a list of coroutines concurrently, printing progress as each finishes."""
    results = []
    total = len(tasks)
    for idx, coro in enumerate(asyncio.as_completed(tasks)):
        result = await coro
        results.append(result)
        logger.info(f"{label} progress: {idx + 1}/{total}")
    return results


async def async_main():
    semaphore = asyncio.Semaphore(CONC_LIMIT)

    async with aiohttp.ClientSession() as session:
        area_urls = await get_area_urls(session, semaphore)
        logger.info(f"Found {len(area_urls)} areas.")
        # area_urls = [TEST_URL_AREA]  # FOR DEBUG

        area_tasks = [scrape_area(session, area, semaphore) for area in area_urls]
        area_results = await gather_progress(area_tasks, "Areas")

        building_links = list({link for links in area_results for link in links})
        logger.info(f"Found {len(building_links)} buildings total.")

        property_tasks = [
            scrape_property(session, link, semaphore) for link in building_links
        ]
        property_results = await gather_progress(property_tasks, "Properties")

    test_data = [row for rows in property_results for row in rows]
    return test_data


def load_cache():
    if GEOCODE_CACHE_PATH.exists():
        return pd.read_csv(GEOCODE_CACHE_PATH)
    return pd.DataFrame(columns=["address", "location", "latitude", "longitude"])


def add_coord_data(ds):
    geolocator = Nominatim(user_agent="hoas_rent_data")
    ds["latitude"] = None
    ds["longitude"] = None
    cache = load_cache()

    for i, row in ds.iterrows():
        mask = (cache["address"] == row["address"]) & (
            cache["location"] == row["location"]
        )

        if mask.any():
            cached = cache.loc[mask].iloc[0]
            latitude = float(cached["latitude"])
            longitude = float(cached["longitude"])
        else:
            time.sleep(2)
            print(f"Cache miss for {row['address']}, {row['location']}. Geocoding...")
            city = row["location"].split(",")[1]
            print(f"Geocoding address: {row['address']}, {city}")
            location = geolocator.geocode(f"{row['address']}, {city}")

            if location is None:
                cache.to_csv(GEOCODE_CACHE_PATH, index=False)
                raise ValueError(
                    f"Could not geocode address: {row['address']}, {row['location']} with return value: {location}"
                )

            latitude = float(location.latitude)
            longitude = float(location.longitude)

            cache = pd.concat(
                [
                    cache,
                    pd.DataFrame(
                        [
                            {
                                "address": row["address"],
                                "location": row["location"],
                                "latitude": latitude,
                                "longitude": longitude,
                            }
                        ]
                    ),
                ],
                ignore_index=True,
            )

        ds.loc[i, "latitude"] = latitude
        ds.loc[i, "longitude"] = longitude

    cache.to_csv(GEOCODE_CACHE_PATH, index=False)
    return ds


def main():
    test_data = asyncio.run(async_main())

    df = pd.DataFrame(test_data)

    for col in AMENITY_COLUMNS:
        if col not in df.columns:
            df[col] = 0
    for col in BASE_COLUMNS:
        if col not in df.columns:
            df[col] = ""

    df = df.reindex(columns=OUTPUT_COLUMNS)

    # format numeric columns
    numeric_cols = ["surface_area", "count", "rent"]

    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="raise")

    # add rent / surface area column
    df["rent_per_m2"] = df["rent"] / df["surface_area"]
    df["rent_per_m2"] = df["rent_per_m2"].round(2)

    # add timestamp column filled for each row with the current date in YYYY-MM-DD format
    date = pd.Timestamp.now().strftime("%Y-%m-%d")
    df["timestamp"] = date

    # add geodata
    df = add_coord_data(df)

    df.to_csv(f"analysis/data/{date}.csv", index=False)
    logger.info(f"Saved {len(df)} rows to analysis/data/{date}.csv")


if __name__ == "__main__":
    main()
