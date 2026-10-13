<?php
declare(strict_types=1);
ini_set('display_errors', '0');
ini_set('display_startup_errors', '0');
set_error_handler(static function(int $severity): never {
    // Do not leak filesystem paths through PHP warnings or their log messages.
    throw new ErrorException('Feedback operation failed', 0, $severity);
});
// PHP >= 8.1. Configure FEEDBACK_PRIVATE_CONFIG in the hosting environment,
// or edit this constant to an absolute path OUTSIDE the document root.
const PRIVATE_CONFIG = '/REPLACE/private/ai-news-feedback/config.php';
const REASONS = ['useful', 'off_topic', 'not_applicable', 'shallow_or_promotional', 'not_new', 'already_covered'];
const NEWSLETTER_REASONS = ['newsletter_balanced', 'newsletter_too_cyber', 'newsletter_too_ai'];
const NEWSLETTER_LABELS = ['Équilibrée', 'Trop cyber', 'Trop IA'];
const LABELS = ['Utile', 'Hors de mes sujets', 'Pas applicable chez moi', 'Trop superficiel / promotionnel', 'Pas assez nouveau', 'Déjà traité'];

function newsletter(array $p): bool { return in_array($p['reason'] ?? null, NEWSLETTER_REASONS, true); }
function newsletter_identity(array $p): bool {
    $day = $p['briefing_date'] ?? null;
    if (!is_string($day)) return false;
    $date = DateTimeImmutable::createFromFormat('!Y-m-d', $day);
    if ($date === false || $date->format('Y-m-d') !== $day) return false;
    return ($p['presentation_id'] ?? null) === hash('sha256', canonical(['scope'=>'newsletter-feedback-v1','briefing_date'=>$day]));
}
function fail(int $status, string $message): never {
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode(['error' => $message]); exit;
}
function output(array $value): never {
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode($value, JSON_THROW_ON_ERROR); exit;
}
function identifier(mixed $value): bool {
    return is_string($value) && preg_match('/\A[a-f0-9]{64}\z/D', $value) === 1;
}
function utc(): string { return (new DateTimeImmutable('now', new DateTimeZone('UTC')))->format('Y-m-d\TH:i:s.u\Z'); }
function timestamp(mixed $value): bool {
    if (!is_string($value) || preg_match('/\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{6})?Z\z/D', $value) !== 1) return false;
    $format = str_contains($value, '.') ? 'Y-m-d\TH:i:s.u\Z' : 'Y-m-d\TH:i:s\Z';
    $date = DateTimeImmutable::createFromFormat('!' . $format, $value, new DateTimeZone('UTC'));
    return $date !== false && $date->format($format) === $value;
}
function canonical(array $p): string { ksort($p); return json_encode($p, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES); }
function sign(array $p, string $secret): string {
    $encoded = rtrim(strtr(base64_encode(canonical($p)), '+/', '-_'), '=');
    return $encoded . '.' . hash_hmac('sha256', $encoded, $secret);
}
function token(mixed $raw, string $secret): array {
    if (!is_string($raw) || strlen($raw) > 4096 || substr_count($raw, '.') !== 1) fail(400, 'Lien invalide');
    [$encoded, $sig] = explode('.', $raw);
    if (!hash_equals(hash_hmac('sha256', $encoded, $secret), $sig)) fail(403, 'Signature invalide');
    $decoded = base64_decode(strtr($encoded, '-_', '+/'), true);
    $p = $decoded === false ? null : json_decode($decoded, true);
    if (!is_array($p) || sign($p, $secret) !== $raw) fail(400, 'Format invalide');
    $required = ['v', 'kind', 'presentation_id', 'reason', 'link_id', 'exp'];
    if (($p['kind'] ?? null) === 'form') $required = array_merge($required, ['submission_id', 'submitted_at']);
    if (newsletter($p)) { $required[] = 'briefing_date'; if (!newsletter_identity($p)) fail(400, 'Newsletter invalide'); }
    $keys = array_keys($p); sort($keys); sort($required);
    if ($keys !== $required || $p['v'] !== 1 || !in_array($p['kind'], ['link', 'form'], true)
        || !identifier($p['presentation_id']) || !identifier($p['link_id'])
        || !in_array($p['reason'], array_merge(REASONS, NEWSLETTER_REASONS), true) || !is_int($p['exp']) || $p['exp'] < time()) fail(400, 'Lien invalide ou expiré');
    if ($p['kind'] === 'form' && (!identifier($p['submission_id']) || !timestamp($p['submitted_at'])
        || $p['submitted_at'] > utc())) fail(400, 'Formulaire invalide');
    return $p;
}
function valid_return(mixed $r, string $name): bool {
    if (!is_array($r)) return false;
    $keys = array_keys($r); sort($keys);
    $expected = ['schema_version','id','submission_id','presentation_id','reason','received_at','submitted_at']; sort($expected);
    if (newsletter($r)) { $expected[]='briefing_date'; sort($expected); if (!newsletter_identity($r)) return false; }
    return $keys === $expected && $r['schema_version'] === 1 && identifier($r['id'])
        && $name === $r['id'] . '.json' && $r['id'] === $r['submission_id']
        && identifier($r['presentation_id']) && in_array($r['reason'], array_merge(REASONS, NEWSLETTER_REASONS), true)
        && timestamp($r['received_at']) && timestamp($r['submitted_at'])
        && $r['submitted_at'] <= $r['received_at'] && $r['received_at'] <= utc();
}
function read_return(string $path, array $cfg): ?array {
    if (is_link($path) || !is_file($path) || filesize($path) > $cfg['max_file_bytes']) return null;
    $data = file_get_contents($path, false, null, 0, $cfg['max_file_bytes'] + 1);
    if ($data === false) return null;
    if (strlen($data) > $cfg['max_file_bytes']) return null;
    $row = json_decode($data, true);
    return valid_return($row, basename($path)) ? $row : null;
}
function page(string $body): never {
    header('Content-Type: text/html; charset=utf-8');
    echo '<!doctype html><html lang="fr"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AI News — Votre avis</title><body><main><h1>Votre avis sur AI News</h1>' . $body . '</main></body></html>'; exit;
}
function esc(string $value): string { return htmlspecialchars($value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8'); }

header('Cache-Control: no-store, private');
header('Referrer-Policy: no-referrer');
header('X-Content-Type-Options: nosniff');
header('X-Frame-Options: DENY');
header('X-Robots-Tag: noindex, nofollow, noarchive, nosnippet');
header("Content-Security-Policy: default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'");
umask(0077);
try {
    if (strlen($_SERVER['QUERY_STRING'] ?? '') > 8192) fail(414, 'Requête trop grande');
    if (($_SERVER['REQUEST_METHOD'] ?? '') === 'POST'
        && ($_SERVER['HTTP_SEC_FETCH_SITE'] ?? '') === 'cross-site') fail(403, 'Origine refusée');
    $configPath = getenv('FEEDBACK_PRIVATE_CONFIG') ?: PRIVATE_CONFIG;
    $realConfig = realpath($configPath);
    $root = realpath($_SERVER['DOCUMENT_ROOT'] ?? '');
    if ($realConfig === false || !$root || str_starts_with($realConfig, $root . DIRECTORY_SEPARATOR)) fail(503, 'Configuration privée indisponible');
    $cfg = require $realConfig;
    if (!is_array($cfg)) fail(503, 'Configuration invalide');
    $confirmation = $cfg['require_confirmation'] ?? true;
    if (!is_bool($confirmation)) fail(503, 'Confirmation invalide');
    $linkSeconds = $cfg['link_seconds'] ?? 7776000;
    if (!is_int($linkSeconds) || $linkSeconds < 1) fail(503, 'Durée de lien invalide');
    $perPresentation = $cfg['max_pending_per_presentation'] ?? 12;
    if (!is_int($perPresentation) || $perPresentation < 1 || $perPresentation > 100) fail(503, 'Limite de corrections invalide');
    foreach (['signing_secret','api_secret'] as $key) {
        if (!is_string($cfg[$key] ?? null) || strlen($cfg[$key]) < 32 || str_contains($cfg[$key], 'REPLACE')) fail(503, 'Secrets non configurés');
    }
    if (hash_equals($cfg['signing_secret'], $cfg['api_secret'])) fail(503, 'Secrets distincts requis');
    foreach (['max_files','max_file_bytes','max_request_bytes','max_batch','form_seconds'] as $key) {
        if (!is_int($cfg[$key] ?? null) || $cfg[$key] < 1) fail(503, 'Limites invalides');
    }
    $dir = realpath($cfg['pending_dir']);
    if (!$dir || !is_dir($dir) || !is_writable($dir) || $dir === $root || str_starts_with($dir, $root . DIRECTORY_SEPARATOR)) fail(503, 'Stockage privé indisponible');
    if ((int)($_SERVER['CONTENT_LENGTH'] ?? 0) > $cfg['max_request_bytes']) fail(413, 'Requête trop grande');
    $action = $_GET['action'] ?? 'vote';
    $method = $_SERVER['REQUEST_METHOD'];
    if ($action === 'pull' || $action === 'ack') {
        if (!hash_equals('Bearer ' . $cfg['api_secret'], $_SERVER['HTTP_AUTHORIZATION'] ?? '')) fail(403, 'Authentification requise');
        if (($action === 'pull' && $method !== 'GET') || ($action === 'ack' && $method !== 'POST')) fail(405, 'Méthode invalide');
        if ($action === 'pull') {
            $limit = filter_var($_GET['limit'] ?? $cfg['max_batch'], FILTER_VALIDATE_INT);
            if ($limit === false || $limit < 1 || $limit > min(500, $cfg['max_batch'])) fail(400, 'Lot invalide');
            $rows = []; $invalid = [];
            foreach (glob($dir . '/*.json') ?: [] as $path) {
                $r = read_return($path, $cfg);
                if ($r === null) { $invalid[] = basename($path); continue; }
                $rows[] = $r;
            }
            usort($rows, fn($a,$b) => [$a['submitted_at'],$a['id']] <=> [$b['submitted_at'],$b['id']]);
            output(['schema_version'=>1, 'returns'=>array_slice($rows,0,$limit), 'invalid_count'=>count($invalid), 'invalid_files'=>array_slice($invalid,0,20)]);
        }
        $raw = file_get_contents('php://input', false, null, 0, $cfg['max_request_bytes'] + 1);
        if ($raw === false || strlen($raw) > $cfg['max_request_bytes']) fail(413, 'Requête trop grande');
        $body = json_decode($raw,true);
        $ids = $body['ids'] ?? null;
        if (!is_array($ids) || !array_is_list($ids) || count($ids) > min(500,$cfg['max_batch'])) fail(400, 'Identifiants invalides');
        foreach ($ids as $id) if (!identifier($id)) fail(400, 'Identifiant invalide');
        $lock = fopen($dir . '/.lock','c');
        if ($lock === false || !flock($lock,LOCK_EX | LOCK_NB)) fail(503, 'Verrou indisponible');
        $deleted=[]; $absent=[]; $errors=[];
        foreach (array_unique($ids) as $id) {
            $path = $dir . '/' . $id . '.json';
            if (!file_exists($path)) { $absent[]=$id; continue; }
            if (is_link($path) || !unlink($path)) $errors[]=$id; else $deleted[]=$id;
        }
        flock($lock,LOCK_UN); fclose($lock);
        output(['schema_version'=>1,'deleted'=>$deleted,'absent'=>$absent,'errors'=>$errors]);
    }
    if ($action !== 'vote' || !in_array($method,['GET','POST'],true)) fail(405, 'Action invalide');
    $post = [];
    if ($method === 'POST') {
        $raw = file_get_contents('php://input', false, null, 0, $cfg['max_request_bytes'] + 1);
        if ($raw === false || strlen($raw) > $cfg['max_request_bytes']) fail(413, 'Requête trop grande');
        parse_str($raw, $post);
    }
    $rawToken = $method === 'GET' ? ($_GET['token'] ?? null) : ($post['token'] ?? null);
    $p = token($rawToken,$cfg['signing_secret']);
    $direct = $method === 'GET' && !$confirmation && ($_GET['confirm'] ?? '') !== '1';
    if ($direct) {
        if ($p['kind'] !== 'link') fail(400,'Lien requis');
        $p['kind']='form';
        $p['submission_id']=hash('sha256','direct-v1:'.$p['link_id'].':'.$p['reason']);
        // Stable signed-link date: a replay after purge remains the same submission.
        $p['submitted_at']=gmdate('Y-m-d\TH:i:s', $p['exp']-$linkSeconds).'.000000Z';
        if (strtotime($p['submitted_at']) > time()) fail(400,'Date de lien invalide');
    } elseif ($method === 'GET') {
        if ($p['kind'] !== 'link') fail(400,'Lien requis');
        $p['kind']='form'; $p['submission_id']=bin2hex(random_bytes(32)); $p['submitted_at']=utc();
        $p['exp']=min($p['exp'],time()+$cfg['form_seconds']);
        $forms='';
        $reasons = newsletter($p) ? NEWSLETTER_REASONS : REASONS;
        $labels = newsletter($p) ? NEWSLETTER_LABELS : LABELS;
        foreach ($reasons as $i=>$reason) {
            $f=$p; $f['reason']=$reason;
            // Motif is signed separately; the submission identity remains unchanged.
            $forms.='<form method="post" action="?action=vote"><input type="hidden" name="token" value="'.esc(sign($f,$cfg['signing_secret'])).'"><button type="submit">'.($reason===$p['reason']?'Confirmer : ':'').esc($labels[$i]).'</button></form>';
        }
        page('<p>Choisissez un motif. Aucun commentaire nécessaire.</p>'.$forms);
    }
    if ($p['kind'] !== 'form') fail(400,'Confirmation requise');
    $id=$p['submission_id'];
    $r=['schema_version'=>1,'id'=>$id,'submission_id'=>$id,'presentation_id'=>$p['presentation_id'],
        'reason'=>$p['reason'],'received_at'=>utc(),'submitted_at'=>$p['submitted_at']];
    if (newsletter($p)) $r['briefing_date']=$p['briefing_date'];
    $lock=fopen($dir.'/.lock','c');
    if ($lock===false || !flock($lock,LOCK_EX | LOCK_NB)) fail(503,'Verrou indisponible');
    $path=$dir.'/'.$id.'.json';
    if (file_exists($path)) {
        $old=read_return($path,$cfg);
        if ($old===null || $old['presentation_id']!==$r['presentation_id'] || $old['reason']!==$r['reason'] || $old['submitted_at']!==$r['submitted_at']) fail(409,'Soumission déjà utilisée ; ouvrez une nouvelle page pour corriger');
    } else {
        $files=glob($dir.'/*.json') ?: [];
        if (count($files) >= $cfg['max_files']) fail(507,'File pleine ; avis non enregistré');
        $count=0;
        foreach ($files as $file) {
            $existing=read_return($file,$cfg);
            if ($existing !== null && $existing['presentation_id']===$r['presentation_id']) $count++;
        }
        if ($count >= $perPresentation) fail(429,'Trop de corrections en attente ; réessayez après synchronisation');
        $data=json_encode($r,JSON_THROW_ON_ERROR);
        if (strlen($data)>$cfg['max_file_bytes']) fail(413,'Retour trop grand');
        $temp=tempnam($dir,'.writing-');
        if ($temp===false) fail(503,'Écriture impossible');
        $stream=fopen($temp,'wb');
        if ($stream===false) { unlink($temp); fail(503,'Écriture impossible'); }
        $written=fwrite($stream,$data); $flushed=fflush($stream); $synced=fsync($stream); fclose($stream);
        if ($written!==strlen($data) || !$flushed || !$synced || !rename($temp,$path)) {
            if (file_exists($temp)) unlink($temp); fail(503,'Avis non enregistré');
        }
    }
    flock($lock,LOCK_UN); fclose($lock);
    // A new GET intentionally creates a new form/submission for correction.
    $p['kind']='link'; unset($p['submission_id'],$p['submitted_at']);
    $again='?action=vote&confirm=1&token='.rawurlencode(sign($p,$cfg['signing_secret']));
    page('<p>Votre avis a été enregistré.</p><a href="'.esc($again).'">Modifier mon choix</a>');
} catch (Throwable $e) {
    // Never include exception text, tokens or paths in public responses/logs.
    fail(503,'Service temporairement indisponible');
}
