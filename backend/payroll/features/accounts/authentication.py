from rest_framework.authentication import SessionAuthentication


class PayrollSessionAuthentication(SessionAuthentication):
    def authenticate_header(self, request):
        # DRF uses this challenge to distinguish a missing session (401) from a denied action (403).
        return "Session"
