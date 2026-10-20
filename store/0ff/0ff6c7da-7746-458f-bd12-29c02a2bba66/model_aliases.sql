ATTACH TABLE _ UUID '23ab6e7f-2d6f-43d9-9f2f-f4e03d6a5427'
(
    `model_raw` String,
    `model_family` String
)
ENGINE = MergeTree
ORDER BY (tuple())
SETTINGS index_granularity = 8192
