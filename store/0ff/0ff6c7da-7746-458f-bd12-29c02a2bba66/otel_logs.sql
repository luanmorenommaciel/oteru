ATTACH TABLE _ UUID 'd3d70521-513f-4fac-96cb-2525c70c4a93'
(
    `Timestamp` DateTime64(9),
    `Body` String,
    `ServiceName` String,
    `ResourceAttributes` Map(String, String),
    `LogAttributes` Map(String, String)
)
ENGINE = MergeTree
ORDER BY Timestamp
SETTINGS index_granularity = 8192
