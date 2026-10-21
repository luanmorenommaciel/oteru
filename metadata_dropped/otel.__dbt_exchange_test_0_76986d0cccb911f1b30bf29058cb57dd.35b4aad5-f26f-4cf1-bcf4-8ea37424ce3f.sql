ATTACH TABLE _ UUID '35b4aad5-f26f-4cf1-bcf4-8ea37424ce3f'
(
    `test` String
)
ENGINE = MergeTree
ORDER BY tuple()
SETTINGS index_granularity = 8192
