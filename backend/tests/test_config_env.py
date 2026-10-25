"""`.env` lines like `LLM_MODEL=     # note`: the reader returns the note as the value; settings must read it as empty."""

from trader.config import Settings


def test_a_comment_in_place_of_a_value_means_empty(tmp_path):
    env = tmp_path / ".env"
    env.write_text("LLM_MODEL=                     # empty = the provider's default\n"
                   "SIGNUP_INVITE_CODE=           # set a code to make sign-up invite-only\n"
                   "CREDENTIAL_KEY=               # encrypts customers' broker keys\n"
                   "ADMIN_EMAILS=boss@x.com  # comma list\n")
    st = Settings(_env_file=env)
    assert st.llm_model == "" and st.signup_invite_code == "" and st.credential_key == ""
    assert "boss@x.com" in st.admin_email_set  # a real value with a trailing comment still works


def test_real_values_are_untouched():
    st = Settings(_env_file=None, llm_model="claude-x", signup_invite_code="#secret")
    assert st.llm_model == "claude-x" and st.signup_invite_code == "#secret"
