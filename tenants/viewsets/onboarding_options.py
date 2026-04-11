"""
Onboarding Options ViewSet for providing country, state, and industry options.

These endpoints are publicly accessible (AllowAny) for use during tenant registration.
Uses pycountry (ISO 3166) standard library for countries and subdivisions.
"""

import pycountry
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

# Industry options for tenant onboarding
INDUSTRY_CHOICES = [
    {"value": "ecommerce", "label": "E-commerce & Retail"},
    {"value": "healthcare", "label": "Healthcare & Medical"},
    {"value": "education", "label": "Education & E-learning"},
    {"value": "finance", "label": "Finance & Banking"},
    {"value": "real_estate", "label": "Real Estate"},
    {"value": "travel", "label": "Travel & Hospitality"},
    {"value": "food", "label": "Food & Restaurant"},
    {"value": "automotive", "label": "Automotive"},
    {"value": "logistics", "label": "Logistics & Transportation"},
    {"value": "entertainment", "label": "Entertainment & Media"},
    {"value": "technology", "label": "Technology & Software"},
    {"value": "manufacturing", "label": "Manufacturing"},
    {"value": "consulting", "label": "Consulting & Professional Services"},
    {"value": "nonprofit", "label": "Non-profit & NGO"},
    {"value": "government", "label": "Government & Public Sector"},
    {"value": "insurance", "label": "Insurance"},
    {"value": "telecom", "label": "Telecommunications"},
    {"value": "agriculture", "label": "Agriculture"},
    {"value": "energy", "label": "Energy & Utilities"},
    {"value": "fashion", "label": "Fashion & Apparel"},
    {"value": "sports", "label": "Sports & Fitness"},
    {"value": "beauty", "label": "Beauty & Personal Care"},
    {"value": "legal", "label": "Legal Services"},
    {"value": "hr", "label": "HR & Recruitment"},
    {"value": "marketing", "label": "Marketing & Advertising"},
    {"value": "other", "label": "Other"},
]


def get_all_countries():
    """Get all countries from pycountry (ISO 3166-1)."""
    return [{"value": country.alpha_2, "label": country.name} for country in pycountry.countries]


def get_subdivisions_for_country(country_code: str):
    """
    Get all subdivisions (states/provinces/regions) for a country.

    Uses pycountry's ISO 3166-2 subdivisions database.
    Returns subdivisions sorted by name.
    """
    try:
        subdivisions = pycountry.subdivisions.get(country_code=country_code.upper())
        if not subdivisions:
            return []

        # Extract the subdivision code (part after the hyphen, e.g., "IN-MH" -> "MH")
        result = [
            {
                "value": sub.code.split("-")[-1],
                "label": sub.name,
                "type": getattr(sub, "type", None),
            }
            for sub in subdivisions
        ]
        # Sort by label (name)
        return sorted(result, key=lambda x: x["label"])
    except (KeyError, LookupError):
        return []


class OnboardingOptionsViewSet(viewsets.ViewSet):
    """
    ViewSet for providing onboarding options (countries, states, industries).

    All endpoints are publicly accessible for use during tenant registration.
    Uses pycountry for ISO 3166 standard country and subdivision data.
    """

    permission_classes = [AllowAny]
    authentication_classes = []  # No authentication required

    @action(detail=False, methods=["get"], url_path="countries")
    def get_countries(self, request):
        """
        Get list of all countries (ISO 3166-1).

        Returns a list of countries with alpha-2 code and name.

        Query params:
            - search: Filter countries by name (case-insensitive)

        Response:
        [
            {"value": "IN", "label": "India"},
            {"value": "US", "label": "United States"},
            ...
        ]
        """
        search = request.query_params.get("search", "").lower()

        country_list = get_all_countries()

        if search:
            country_list = [c for c in country_list if search in c["label"].lower() or search in c["value"].lower()]

        return Response(country_list, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="states")
    def get_states(self, request):
        """
        Get list of states/provinces/subdivisions for a specific country (ISO 3166-2).

        Query params:
            - country: Country code (e.g., "IN", "US", "GB", "CA") - required
            - search: Filter subdivisions by name (case-insensitive)
            - type: Filter by subdivision type (e.g., "State", "Province", "Territory")

        Response:
        [
            {"value": "MH", "label": "Maharashtra", "type": "State"},
            {"value": "DL", "label": "Delhi", "type": "Union territory"},
            ...
        ]

        Supports ALL countries with ISO 3166-2 subdivisions.
        """
        country_code = request.query_params.get("country", "").upper()
        search = request.query_params.get("search", "").lower()
        subdivision_type = request.query_params.get("type", "").lower()

        if not country_code:
            return Response(
                {"error": "country query parameter is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate country code exists
        try:
            pycountry.countries.get(alpha_2=country_code)
        except (KeyError, LookupError):
            return Response(
                {"error": f"Invalid country code: {country_code}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        states = get_subdivisions_for_country(country_code)

        if search:
            states = [s for s in states if search in s["label"].lower() or search in s["value"].lower()]

        if subdivision_type:
            states = [s for s in states if s.get("type", "").lower() == subdivision_type]

        return Response(states, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="industries", permission_classes=[AllowAny])
    def get_industries(self, request):
        """
        Get list of available industries.

        Query params:
            - search: Filter industries by name (case-insensitive)

        Response:
        [
            {"value": "ecommerce", "label": "E-commerce & Retail"},
            {"value": "healthcare", "label": "Healthcare & Medical"},
            ...
        ]
        """
        search = request.query_params.get("search", "").lower()

        industries = INDUSTRY_CHOICES.copy()

        if search:
            industries = [i for i in industries if search in i["label"].lower() or search in i["value"].lower()]

        return Response(industries, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="all")
    def get_all_options(self, request):
        """
        Get all onboarding options in a single request.

        Query params:
            - countries_for_states: Comma-separated country codes to include states for
                                   (default: "IN,US")

        Response:
        {
            "countries": [...],
            "industries": [...],
            "states": {
                "IN": [...],
                "US": [...]
            }
        }
        """
        # Get country codes for which to fetch states
        countries_param = request.query_params.get("countries_for_states", "IN,US")
        country_codes = [c.strip().upper() for c in countries_param.split(",") if c.strip()]

        states_by_country = {}
        for code in country_codes:
            subdivisions = get_subdivisions_for_country(code)
            if subdivisions:
                states_by_country[code] = subdivisions

        return Response(
            {
                "countries": get_all_countries(),
                "industries": INDUSTRY_CHOICES,
                "states": states_by_country,
            },
            status=status.HTTP_200_OK,
        )
