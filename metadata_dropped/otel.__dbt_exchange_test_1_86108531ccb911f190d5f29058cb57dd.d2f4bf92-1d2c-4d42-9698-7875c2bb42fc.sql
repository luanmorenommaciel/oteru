ATTACH TABLE _ UUID 'd2f4bf92-1d2c-4d42-9698-7875c2bb42fc'
(
    `test` String
)
ENGINE = MergeTree
ORDER BY tuple()
SETTINGS index_granularity = 8192
