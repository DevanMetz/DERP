from allauth.account.adapter import DefaultAccountAdapter


class ClosedSignupAccountAdapter(DefaultAccountAdapter):
    """Keep self-hosted installations invite/admin provisioned."""

    def is_open_for_signup(self, request):
        return False
