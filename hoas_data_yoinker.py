import requests
from bs4 import BeautifulSoup
import pandas as pd

BASE_URL = "https://hoas.fi"
AREAS_URL = "https://hoas.fi/alueet/"
TEST_URL_AREA = "https://hoas.fi/alueet/kannelmaki/"
TEST_URL_PROPERTY = "https://hoas.fi/kohteet/kitarakuja-1/"

def get_area_urls(areas_url=AREAS_URL):
    """Gets all the area urls.

    Args:
        listings_url (str, optional): url to scrape. Defaults to LISTINGS_URL.

    Returns:
        list: list of links to areas.
    """
    source_code = requests.get(areas_url)
    if source_code.status_code != 200:
        return []   # fail to get the page
    
    plain_text = source_code.text  
    soup = BeautifulSoup(plain_text, 'html.parser')

    links = soup.find_all('a', href=True)
    links = [link['href'] for link in links if '/alueet/' in link['href']]
    links = [link for link in links if link != AREAS_URL]  # Remove the base url if scraped

    return list(set(links))  # Remove duplicates


def scrape_area(area_url):
    print("-"*50)
    print(f"Scraping area: {area_url.removeprefix('https://hoas.fi/alueet')}")
    source_code = requests.get(area_url)
    if source_code.status_code != 200:
        return []   # fail to get the page
    
    plain_text = source_code.text  
    soup = BeautifulSoup(plain_text, 'html.parser')

    links = soup.find_all('a', href=True)
    links = [link['href'] for link in links if '/kohteet/' in link['href']]
    links = [link for link in links if link != 'https://hoas.fi/kohteet/']  # Remove the base url if scraped

    return list(set(links))  # Remove duplicates

def scrape_property(property_url):
    print(f"Scraping property: {property_url.removeprefix('https://hoas.fi/kohteet')}")

    source_code = requests.get(property_url)
    if source_code.status_code != 200:
        return []   # fail to get the page
    
    plain_text = source_code.text  
    soup = BeautifulSoup(plain_text, 'html.parser')

    data = []

    # Extracting services
    services = soup.find('div', class_='services_list')
    if services:
        services = services.text.strip().split('\n')
        services = [service.strip() for service in services if service.strip() != '']
        # remove the first element, which is the header
        services.pop(0)
        services

    # extracting the condition according to hoas:
    condition_span = soup.find('span', string=lambda x: x and 'Kohteen kunto:' in x)

    if condition_span:
        condition_text = condition_span.text.strip()
        condition_text = condition_text.removeprefix('Kohteen kunto: ')
    else:
        condition_text = "no condition"

    # extracting basic info
    basic_info = soup.find('div', class_='property-table w-100 col-12')
    if basic_info:
        basic_info = basic_info.find_all('div', class_='row')
        basic_dict = {}
        for row in basic_info:
            key = row.find('div', class_='col-12 col-md-3').text.strip()
            value = row.find('div', class_='col-12 col-md-9').text.strip()
            basic_dict[key] = value
        energialuokka = basic_dict['Energialuokka']
        perusparannusvuosi = basic_dict['Perusparannusvuosi'].split(', ')[-1]
        rakennusvuosi = basic_dict['Rakennusvuosi'].split(', ')[-1]
            

    # extracting the location
    location = soup.find('span', class_='location').text.strip()

    # extracting the ratings:
    rating = soup.find('span', class_='rating')
    if rating:
        rating = rating.text.strip().removesuffix('/5')
    else:
        rating = "no rating"

    
    # extracting apt info
    apartment_box = soup.find('div', class_='element-property-apartments-listing--content')
    if apartment_box:
        types = apartment_box.find_all('div', class_='single-container')    # lists all the types of apartments, e.g. 1-room, 2-room
        for type in types:  # iterate through each type
            type_name = type.find('div', class_='type').text.strip()
            single_type = type.find_all('div', class_='element-block apartment-info')
            for single in single_type:
                address_n_rooms = single.find('div', class_='apartment-address').text.strip()
                address = address_n_rooms.split(', ')[0]
                rooms = address_n_rooms.split(', ')[-1]
                surface_area = single.find('div', class_='surface-area').text.strip().removesuffix(' m²')
                count = single.find('div', class_='count').text.strip().removesuffix(' kpl')
                rent = single.find('div', class_='rent').text.strip().removesuffix(' €')



                data += [[location, services, energialuokka, condition_text, perusparannusvuosi, rakennusvuosi, type_name, address, rooms, surface_area, count, rent, rating]]



    

    return data

def main():


    area_urls = get_area_urls()
    print(f"Found {len(area_urls)} areas.")
    test_data = []
    #area_urls = [TEST_URL_AREA] FOR DEBUG
    for idx, area in enumerate(area_urls):
        print(f"Progress: {idx+1}/{len(area_urls)}")
        building_links = scrape_area(area)
        print(f"Found {len(building_links)} buildings.")
        for link in building_links:
            test_data += scrape_property(link)
    
    df = pd.DataFrame(test_data, columns=['location', 'services', 'energy_class', 'condition', 'renovation_year', 'building_year', 'type', 'address', 'rooms', 'surface_area', 'count', 'rent', 'rating'])

    # Find all unique amenities
    unique_amenities = set(amenity for sublist in df['services'] for amenity in sublist)

    # Create new columns for each amenity
    for amenity in unique_amenities:
        df[amenity] = df['services'].apply(lambda x: 1 if amenity in x else 0)

    #  the original amenities list column
    df.drop(columns=['services'], inplace=True)

    # Save to CSV
    df.to_csv('with_grade.csv', index=False)
    



if __name__ == "__main__":
    main()
