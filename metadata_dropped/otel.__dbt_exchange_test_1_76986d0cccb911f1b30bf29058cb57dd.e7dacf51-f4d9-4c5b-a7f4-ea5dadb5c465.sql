ATTACH TABLE _ UUID 'e7dacf51-f4d9-4c5b-a7f4-ea5dadb5c465'
(
    `test` String
)
ENGINE = MergeTree
ORDER BY tuple()
SETTINGS index_granularity = 8192
