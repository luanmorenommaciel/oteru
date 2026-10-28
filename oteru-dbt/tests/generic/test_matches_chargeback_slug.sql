{#- #48: valores PREENCHIDOS (!= '') devem casar com o slug da #46; '' é permitido
    (scope que não usa o campo). Coerência scope × campos é outro teste. -#}
{% test matches_chargeback_slug(model, column_name) %}
SELECT {{ column_name }} AS value
FROM {{ model }}
WHERE {{ column_name }} != ''
  AND NOT match({{ column_name }}, '{{ chargeback_slug_regex() }}')
{% endtest %}
