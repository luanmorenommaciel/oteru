{#- #46: regex únicos de pseudônimo. Fonte única para os testes de PII. -#}
{% macro chargeback_seed_actor_regex() %}^hmac:v[0-9]+:[0-9a-f]{64}${% endmacro %}
{% macro chargeback_model_actor_regex() %}^hmac:v1:[0-9a-f]{64}${% endmacro %}
