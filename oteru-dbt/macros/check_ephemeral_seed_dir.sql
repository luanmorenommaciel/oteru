{# #107: fail-closed OTERU_EPHEMERAL_SEED_DIR. Never silently fall back to gabarito. #}
{% macro check_ephemeral_seed_dir() %}
  {% set raw = env_var('OTERU_EPHEMERAL_SEED_DIR', '__UNSET__') %}
  {% if raw == '' %}
    {{ exceptions.raise_compiler_error(
         'OTERU_EPHEMERAL_SEED_DIR is empty; refusing to fall back to gabarito'
       ) }}
  {% elif raw != '__UNSET__' %}
    {# Relative paths and anything under the project/gabarito are in-repo. #}
    {% set n = raw | replace('\\', '/') %}
    {% if not n.startswith('/')
          or '/oteru-dbt/' in n
          or n.endswith('/oteru-dbt')
          or n == 'seeds'
          or n.endswith('/seeds')
          or '/seeds/' in n
          or 'chargeback_fixtures' in n %}
      {{ exceptions.raise_compiler_error(
           'OTERU_EPHEMERAL_SEED_DIR is inside the repository; refusing to fall back to gabarito'
         ) }}
    {% endif %}
  {% endif %}
{% endmacro %}
