<?php
// Copy outside DOCUMENT_ROOT; never put real secrets in this tracked example.
return [
    'require_confirmation' => true, // false: experimental GET voting, including mail scanners.
    'link_seconds' => 7776000, // Must match email link_days (90 days).
    'signing_secret' => 'REPLACE_WITH_AT_LEAST_32_RANDOM_BYTES',
    'api_secret' => 'REPLACE_WITH_A_DIFFERENT_RANDOM_SECRET',
    'pending_dir' => __DIR__ . '/pending',
    'max_files' => 10000,
    'max_pending_per_presentation' => 12,
    'max_file_bytes' => 4096,
    'max_request_bytes' => 65536,
    'max_batch' => 500,
    'form_seconds' => 1800,
];
