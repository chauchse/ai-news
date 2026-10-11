#!/usr/bin/env python3
"""AI News V56: offline speech with Piper, Kokoro or XTTS-v2."""
import argparse
from datetime import datetime
from pathlib import Path
import re
import subprocess
import tempfile
import wave
from zoneinfo import ZoneInfo
import yaml


def extract(text):
    items, section, title, summary = [], 'Actualités', None, []

    def flush():
        if title and summary:
            items.append((section, title, ' '.join(summary)))

    for raw in text.splitlines():
        line = raw.strip()
        match = re.match(r'^#{2,3}\s+\d+\.\s+(.+)$', line)
        if match:
            flush()
            title, summary = match.group(1).strip(), []
            continue
        if re.match(r'^#{2,3}\s+', line):
            flush()
            title, summary = None, []
            heading = re.sub(r'^#{2,3}\s+', '', line)
            if re.search(r'annexe|non retenu|autres alertes', heading, re.I):
                break
            section = heading
            continue
        if not line or line.startswith(('#', '**', '- ', '|')) or re.match(r'^(Score|Sources)\s*:', line, re.I):
            continue
        if title:
            summary.append(re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', line))
    flush()
    return items


def run(command, *, input_text=None):
    subprocess.run(command, input=input_text, text=True, check=True, stdout=subprocess.DEVNULL)


def silence(path, ms, rate):
    with wave.open(str(path), 'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(b'\0\0' * int(rate * ms / 1000))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='/config/audio.yml')
    args = parser.parse_args()
    config = yaml.safe_load(Path(args.config).read_text(encoding='utf-8')) or {}
    engine = config.get('engine', 'piper')
    language = config.get('language', 'fr')
    if engine not in ('piper', 'kokoro', 'xtts'):
        parser.error('engine doit être piper, kokoro ou xtts')
    voice = config.get('voices', {}).get(engine, {}).get(language)
    if not voice:
        parser.error(f'Voix absente pour {engine}/{language}')
    pause_ms = int(config.get('pause_ms', 1200))
    title_pause_ms = int(config.get('title_pause_ms', 600))
    bitrate = config.get('bitrate', '32k')
    if bitrate not in ('24k', '32k', '40k', '48k') or not all(0 <= x <= 10000 for x in (pause_ms, title_pause_ms)):
        parser.error('Bitrate ou pauses invalides')
    items = extract(Path(config.get('input', '/output/briefing.md')).read_text(encoding='utf-8'))
    if not items:
        parser.error('No articles found in Markdown')
    configured_output = Path(config.get('output', '/output/briefing.mp3'))
    dated = datetime.now(ZoneInfo('Europe/Paris')).strftime('%Y-%m-%d')
    output = configured_output.with_name(f'{dated}-{configured_output.name}')
    output.parent.mkdir(parents=True, exist_ok=True)

    # Load only the selected engine, once per briefing.
    if engine == 'piper':
        if not Path(voice).is_file():
            parser.error(f'Modèle Piper introuvable : {voice}')
        rate = 22050

        def synth(text, path):
            run(['piper', '--model', voice, '--output_file', str(path)], input_text=text + '\n')

    elif engine == 'kokoro':
        import numpy as np
        import soundfile as sf
        from kokoro import KModel, KPipeline
        base = Path('/models/kokoro')
        model = KModel(config=str(base / 'config.json'), model=str(base / 'kokoro-v1_0.pth')).eval()
        lang_code = {'fr': 'f', 'en': 'a' if voice.startswith('a') else 'b'}.get(language)
        if not lang_code:
            parser.error(f'Langue Kokoro non prise en charge : {language}')
        pipeline = KPipeline(lang_code=lang_code, model=model, repo_id='hexgrad/Kokoro-82M')
        voice_path = base / 'voices' / f'{voice}.pt'
        if not voice_path.is_file():
            parser.error(f'Voix Kokoro introuvable : {voice_path}')
        import torch
        voice_tensor = torch.load(voice_path, map_location='cpu', weights_only=True)
        rate = 24000

        def synth(text, path):
            chunks = [audio.detach().cpu().numpy() for _, _, audio in pipeline(text, voice=voice_tensor)]
            if not chunks:
                raise RuntimeError('Kokoro n’a produit aucun son')
            sf.write(str(path), np.concatenate(chunks), rate, subtype='PCM_16')

    else:
        from TTS.api import TTS
        base = Path('/models/xtts')
        #tts = TTS(model_path=str(base / 'model.pth'), config_path=str(base / 'config.json'), progress_bar=False).to('cpu')
        tts = TTS(model_name='tts_models/multilingual/multi-dataset/xtts_v2', progress_bar=False).to('cpu')
        rate = 24000
        reference = config.get('xtts_speaker_wav')
        if reference and not Path(reference).is_file():
            parser.error(f'Référence vocale XTTS introuvable : {reference}')

        def synth(text, path):
            kw = {'speaker_wav': reference} if reference else {'speaker': voice}
            tts.tts_to_file(text=text, file_path=str(path), language=language, **kw)

    with tempfile.TemporaryDirectory() as temp:
        temp = Path(temp)
        wavs = []
        counter = 0

        def speak(text):
            nonlocal counter
            counter += 1
            path = temp / f'{counter:04d}.wav'
            synth(text, path)
            wavs.append(path)

        def pause(ms):
            nonlocal counter
            if ms:
                counter += 1
                path = temp / f'{counter:04d}.wav'
                silence(path, ms, rate)
                wavs.append(path)

        speak('Votre briefing de veille technologique et de sécurité.' if language == 'fr' else 'Your technology and security briefing.')
        pause(pause_ms)
        previous_section = None
        for section, title, summary in items:
            if section != previous_section:
                speak(section + '.')
                pause(title_pause_ms)
                previous_section = section
            speak(title + '.')
            pause(title_pause_ms)
            speak(summary)
            pause(pause_ms)
        playlist = temp / 'concat.txt'
        playlist.write_text(''.join(f"file '{f}'\n" for f in wavs), encoding='utf-8')
        run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', str(playlist), '-ar', '22050', '-ac', '1', '-c:a', 'libmp3lame', '-b:a', bitrate, str(output)])
    print(f'MP3: {output} ({output.stat().st_size / 1048576:.2f} MiB, {len(items)} articles, {engine}/{language})')


if __name__ == '__main__':
    main()
