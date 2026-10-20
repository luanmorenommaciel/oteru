ATTACH TABLE _ UUID '1b42e9f8-bad3-4e71-be67-7fb0787b9221'
(
    `test` String
)
ENGINE = MergeTree
ORDER BY tuple()
SETTINGS index_granularity = 8192
