import json
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
from geopy.geocoders import Nominatim

GEOCODE_CACHE_PATH = Path("geocode_cache.csv")
INPUT_CSV_PATH = Path("output.csv")
OUTPUT_WITH_COORDS_PATH = Path("output_with_coords.csv")
OUTPUT_HTML_PATH = Path("output/index.html")


def load_ds(src=INPUT_CSV_PATH):
    ds = pd.read_csv(src)
    ds["rent_per_m2"] = ds["rent"] / ds["surface_area"]
    ds["rent_per_m2"] = ds["rent_per_m2"].round(2)
    return ds


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


def build_html(ds, output_path=OUTPUT_HTML_PATH):
    output_path.parent.mkdir(parents=True, exist_ok=True)

    table_columns = [
        {"title": "Address", "field": "address", "headerFilter": "input"},
        {"title": "Location", "field": "location", "headerFilter": "input"},
        {"title": "Type", "field": "type", "headerFilter": "input"},
        {"title": "Surface (m²)", "field": "surface_area", "sorter": "number"},
        {"title": "Rent (€)", "field": "rent", "sorter": "number"},
        {"title": "€/m²", "field": "rent_per_m2", "sorter": "number"},
        {"title": "Count", "field": "count", "sorter": "number"},
    ]

    records = ds.where(pd.notnull(ds), None).to_dict(orient="records")
    json_data = json.dumps(records, ensure_ascii=False)
    json_columns = json.dumps(table_columns, ensure_ascii=False)
    last_updated = datetime.now().strftime("%Y-%m-%d %H:%M")

    html = f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
    <meta charset=\"UTF-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
    <title>HOAS Rent Map</title>

    <link href=\"https://unpkg.com/tabulator-tables@5.5.2/dist/css/tabulator.min.css\" rel=\"stylesheet\" />
    <script src=\"https://unpkg.com/tabulator-tables@5.5.2/dist/js/tabulator.min.js\"></script>

    <link rel=\"stylesheet\" href=\"https://unpkg.com/leaflet@1.9.4/dist/leaflet.css\" />
    <script src=\"https://unpkg.com/leaflet@1.9.4/dist/leaflet.js\"></script>

    <style>
        body {{ font-family: Arial, sans-serif; margin: 24px; }}
        #map {{ height: 420px; margin-bottom: 16px; }}
        .toolbar {{ margin-bottom: 12px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
        .toolbar button {{ padding: 6px 10px; cursor: pointer; }}
        #stats {{
            border: 1px solid #ddd;
            background: #fafafa;
            padding: 12px;
            margin-bottom: 16px;
            line-height: 1.6;
        }}
        .map-legend {{
            background: #fff;
            border: 1px solid #ccc;
            border-radius: 4px;
            padding: 8px 10px;
            font-size: 12px;
            line-height: 1.4;
            color: #333;
        }}
        .legend-gradient {{
            width: 140px;
            height: 10px;
            margin: 4px 0;
            background: linear-gradient(to right, hsl(120, 75%, 45%), hsl(0, 75%, 45%));
            border: 1px solid #bbb;
        }}
        .legend-scale {{
            display: flex;
            justify-content: space-between;
            width: 140px;
            font-size: 11px;
        }}
    </style>
</head>
<body>
    <h1>HOAS Apartment Rent Overview</h1>
    <p>Last updated: {last_updated}</p>

    <div id=\"map\"></div>
    <div class=\"toolbar\">
        <button id=\"clear-filter\">Clear filters</button>
    </div>
    <div id=\"stats\"></div>
    <div id=\"rent-table\"></div>

    <script>
        const data = {json_data};
        const columns = {json_columns};

        const makeBuildingKey = (address, location) => `${{address || ""}}|||${{location || ""}}`;

        const table = new Tabulator("#rent-table", {{
            data,
            layout: "fitColumns",
            pagination: "local",
            paginationSize: 15,
            columns,
            initialSort: [{{ column: "rent_per_m2", dir: "asc" }}]
        }});

        const map = L.map("map").setView([60.17, 24.94], 11);
        L.tileLayer("https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
            attribution: "© OpenStreetMap"
        }}).addTo(map);

        const markerGroup = L.featureGroup().addTo(map);
        const statsEl = document.getElementById("stats");
        const buildingMarkersByKey = new Map();

        const numeric = (value) => {{
            const num = Number(value);
            return Number.isFinite(num) ? num : null;
        }};

        const weightOf = (row) => {{
            const countValue = numeric(row.count);
            return countValue !== null && countValue > 0 ? countValue : 1;
        }};

        const formatCurrency = (value) => {{
            if (!Number.isFinite(value)) return "N/A";
            return `€${{value.toFixed(2)}}`;
        }};

        const formatNumber = (value) => Number.isFinite(value) ? value.toFixed(2) : "N/A";

        const buildingMap = new Map();
        data.forEach((row) => {{
            if (row.latitude == null || row.longitude == null) return;
            const key = makeBuildingKey(row.address, row.location);

            if (!buildingMap.has(key)) {{
                buildingMap.set(key, {{
                    address: row.address,
                    location: row.location,
                    latitude: numeric(row.latitude),
                    longitude: numeric(row.longitude),
                    apartments: []
                }});
            }}

            buildingMap.get(key).apartments.push(row);
        }});

        const buildings = Array.from(buildingMap.values()).map((building) => {{
            const apartmentCount = building.apartments.reduce((sum, apartment) => sum + weightOf(apartment), 0);

            const weightedRent = building.apartments.reduce((sum, apartment) => {{
                const rent = numeric(apartment.rent);
                return rent === null ? sum : sum + (rent * weightOf(apartment));
            }}, 0);

            const weightedRentPerM2 = building.apartments.reduce((sum, apartment) => {{
                const rentPerM2 = numeric(apartment.rent_per_m2);
                return rentPerM2 === null ? sum : sum + (rentPerM2 * weightOf(apartment));
            }}, 0);

            const avgRent = apartmentCount > 0 ? weightedRent / apartmentCount : null;
            const avgRentPerM2 = apartmentCount > 0 ? weightedRentPerM2 / apartmentCount : null;

            return {{
                ...building,
                key: makeBuildingKey(building.address, building.location),
                count: apartmentCount,
                avgRent,
                avgRentPerM2
            }};
        }});

        const buildingsByKey = new Map(buildings.map((building) => [building.key, building]));

        const avgRentPerM2Values = buildings
            .map((building) => building.avgRentPerM2)
            .filter((value) => value !== null);

        const minRentPerM2 = avgRentPerM2Values.length ? Math.min(...avgRentPerM2Values) : 0;
        const maxRentPerM2 = avgRentPerM2Values.length ? Math.max(...avgRentPerM2Values) : 1;

        const markerColor = (value) => {{
            if (value === null) return "#777777";
            const denominator = Math.max(maxRentPerM2 - minRentPerM2, 0.0001);
            const normalized = Math.min(1, Math.max(0, (value - minRentPerM2) / denominator));
            const hue = 120 - (normalized * 120);
            return `hsl(${{hue}}, 75%, 45%)`;
        }};

        const markerBaseRadius = (count) => Math.min(18, 5 + (Math.sqrt(count) * 1.8));
        const buildingMarkers = [];

        const showGlobalStats = () => {{
            const apartmentCount = data.reduce((sum, apartment) => sum + weightOf(apartment), 0);

            const weightedRent = data.reduce((sum, apartment) => {{
                const rent = numeric(apartment.rent);
                return rent === null ? sum : sum + (rent * weightOf(apartment));
            }}, 0);

            const weightedRentPerM2 = data.reduce((sum, apartment) => {{
                const rentPerM2 = numeric(apartment.rent_per_m2);
                return rentPerM2 === null ? sum : sum + (rentPerM2 * weightOf(apartment));
            }}, 0);

            const avgRent = apartmentCount > 0 ? weightedRent / apartmentCount : null;
            const avgRentPerM2 = apartmentCount > 0 ? weightedRentPerM2 / apartmentCount : null;

            statsEl.innerHTML = `
                <strong>All apartments</strong><br>
                Apartments: ${{Math.round(apartmentCount)}}<br>
                Buildings: ${{buildings.length}}<br>
                Average rent: ${{formatCurrency(avgRent)}}<br>
                Average rent/m²: ${{formatCurrency(avgRentPerM2)}}
            `;
        }};

        const showBuildingStats = (building) => {{
            statsEl.innerHTML = `
                <strong>${{building.address || "Unknown address"}}</strong><br>
                ${{building.location || ""}}<br>
                Apartments: ${{Math.round(building.count)}}<br>
                Average rent: ${{formatCurrency(building.avgRent)}}<br>
                Average rent/m²: ${{formatCurrency(building.avgRentPerM2)}}
            `;
        }};

        buildings.forEach((building) => {{
            if (building.latitude === null || building.longitude === null) return;

            const color = markerColor(building.avgRentPerM2);
            const baseRadius = markerBaseRadius(building.count);
            const marker = L.circleMarker([building.latitude, building.longitude], {{
                radius: baseRadius,
                color,
                fillColor: color,
                fillOpacity: 0.75,
                weight: 1.5
            }}).addTo(markerGroup);

            marker._baseRadius = baseRadius;
            buildingMarkers.push(marker);
            buildingMarkersByKey.set(building.key, marker);

            marker.bindPopup(`
                <strong>${{building.address || "Unknown address"}}</strong><br>
                ${{building.location || ""}}<br>
                Apartments: ${{Math.round(building.count)}}<br>
                Avg rent: ${{formatCurrency(building.avgRent)}}<br>
                Avg rent/m²: ${{formatCurrency(building.avgRentPerM2)}}
            `);

            marker.on("click", () => {{
                table.setFilter([
                    {{ field: "address", type: "=", value: building.address }},
                    {{ field: "location", type: "=", value: building.location }}
                ]);
                showBuildingStats(building);
            }});
        }});

        table.on("rowClick", (_event, row) => {{
            const rowData = row.getData();
            const key = makeBuildingKey(rowData.address, rowData.location);
            const marker = buildingMarkersByKey.get(key);
            const building = buildingsByKey.get(key);

            if (marker) {{
                const targetZoom = Math.max(map.getZoom(), 14);
                map.flyTo(marker.getLatLng(), targetZoom, {{ duration: 0.6 }});
                marker.openPopup();
            }}

            if (building) {{
                showBuildingStats(building);
            }}
        }});

        document.getElementById("clear-filter").addEventListener("click", () => {{
            table.clearFilter(true);
            showGlobalStats();
        }});

        const legend = L.control({{ position: "bottomright" }});
        legend.onAdd = () => {{
            const div = L.DomUtil.create("div", "map-legend");
            div.innerHTML = `
                <strong>Legend</strong><br>
                Color = avg rent/m²
                <div class="legend-gradient"></div>
                <div class="legend-scale">
                    <span>${{formatNumber(minRentPerM2)}}</span>
                    <span>${{formatNumber(maxRentPerM2)}}</span>
                </div>
                Marker size = apartment count
            `;
            return div;
        }};
        legend.addTo(map);

        const updateMarkerSizesForZoom = () => {{
            const zoom = map.getZoom();
            const zoomFactor = Math.max(0.45, Math.min(1.15, (zoom - 8) / 6));
            buildingMarkers.forEach((marker) => marker.setRadius(marker._baseRadius * zoomFactor));
        }};

        map.on("zoomend", updateMarkerSizesForZoom);
        updateMarkerSizesForZoom();

        showGlobalStats();

        if (markerGroup.getLayers().length > 0) {{
            map.fitBounds(markerGroup.getBounds().pad(0.15));
        }}
    </script>
</body>
</html>
"""

    output_path.write_text(html, encoding="utf-8")


if __name__ == "__main__":
    ds = load_ds()
    ds = add_coord_data(ds)
    build_html(ds, OUTPUT_HTML_PATH)
    print(f"Site built successfully: {OUTPUT_HTML_PATH}")
