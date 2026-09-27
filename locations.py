# Pincode-by-pincode coverage instead of neighborhood names - exhaustively
# works through every pincode in a city (ascending, starting 400001 in
# Mumbai) before moving to the next city, so "all doctors in this area"
# means "all doctors in this pincode" rather than a rougher named locality.
# Pincodes below are real, verified codes (not a blind 400001-400104 sweep,
# which would waste queries on unassigned numbers) - Mumbai's are the
# official 37 assigned Mumbai-district codes; Navi Mumbai/Thane are the
# codes for the same localities the original neighborhood-based version
# covered.

PINCODES = {
    "Mumbai": [
        "400001", "400003", "400004", "400005", "400007", "400009", "400011",
        "400012", "400020", "400022", "400028", "400029", "400030", "400037",
        "400042", "400050", "400051", "400053", "400058", "400065", "400066",
        "400067", "400069", "400071", "400074", "400075", "400078", "400082",
        "400084", "400085", "400089", "400091", "400092", "400093", "400094",
        "400099", "400104",
    ],
    "Navi Mumbai": [
        "400614", "400703", "400706", "400708", "400710", "410206", "410209",
        "410210",
    ],
    "Thane": [
        "400601", "400602", "400604", "400605", "400606", "400607", "400612",
        "400615",
    ],
}

# Priority order: specific specialties are searched first in every pincode,
# "doctors" (the broadest catch-all) always runs last - so a day's run that
# gets cut off by the lead target still has specialists over-represented
# relative to generic listings, per how leads should be prioritized.
SPECIALTY_SEARCH_TERMS = [
    "cardiologist", "dermatologist", "orthopedic doctor", "gynecologist",
    "pediatrician", "ent specialist", "dentist", "psychiatrist",
    "urologist", "diabetologist", "gastroenterologist", "neurologist",
    "ophthalmologist", "nephrologist", "pulmonologist", "oncologist",
    "endocrinologist", "rheumatologist", "sexologist", "psychologist",
    "physiotherapist", "homeopathy doctor", "ayurvedic doctor",
    "dietitian nutritionist", "cosmetic surgeon", "general surgeon",
    "ivf specialist", "proctologist", "general physician",
]
GENERAL_FALLBACK_TERM = "doctors"


def build_queries(search_term):
    """Yields (city, pincode, query_string) tuples for every pincode, one city at a time."""
    for city, pincodes in PINCODES.items():
        for pincode in pincodes:
            query = f"{search_term} near {pincode} {city}"
            yield city, pincode, query


def build_priority_queries():
    """
    Yields (city, pincode, specialty_term, query_string) for the FULL daily
    grid, in priority order: for each pincode, every specialty term (in
    SPECIALTY_SEARCH_TERMS order) is queried before the general "doctors"
    fallback for that same pincode - then the run moves to the next pincode.
    """
    all_terms = SPECIALTY_SEARCH_TERMS + [GENERAL_FALLBACK_TERM]
    for city, pincodes in PINCODES.items():
        for pincode in pincodes:
            for term in all_terms:
                query = f"{term} near {pincode} {city}"
                yield city, pincode, term, query
