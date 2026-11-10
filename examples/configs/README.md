# Example configuration

Configuration examples are kept free of real credentials. The synthetic app
config is in `../synthetic-repository/config/app.example.json`. It shows an
explicit summary model selection and placeholder text only. Set `GROQ_API_KEY`
in the process environment when intentionally running provider-backed summary
work; do not add a key to a checked-in file.

The runtime default home is `~/.mergemonster`; `MERGEMONSTER_HOME` can select an
alternate location. `CODEMERGE_HOME` and historical `~/.codemerge` are honored
for compatibility.
