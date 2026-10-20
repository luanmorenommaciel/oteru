ATTACH TABLE _ UUID 'bba0d728-6e02-4ebf-8e90-3205226e25a9'
(
    `test` String
)
ENGINE = MergeTree
ORDER BY tuple()
SETTINGS index_granularity = 8192
