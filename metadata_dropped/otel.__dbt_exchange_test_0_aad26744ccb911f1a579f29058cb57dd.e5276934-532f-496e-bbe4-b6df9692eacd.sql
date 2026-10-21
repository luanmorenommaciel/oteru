ATTACH TABLE _ UUID 'e5276934-532f-496e-bbe4-b6df9692eacd'
(
    `test` String
)
ENGINE = MergeTree
ORDER BY tuple()
SETTINGS index_granularity = 8192
