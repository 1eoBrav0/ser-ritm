"""Download the portable official Windows tunnel client and verify GitHub's SHA256 digest."""
import hashlib
import json
import os
import urllib.request
from pathlib import Path


def main():
    if os.name != 'nt':
        raise SystemExit('Установи cloudflared для своей системы по https://developers.cloudflare.com/tunnel/downloads/')
    request = urllib.request.Request('https://api.github.com/repos/cloudflare/cloudflared/releases/latest',
        headers={'User-Agent': 'Ser-Ritm-setup', 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        release = json.load(response)
    asset = next(a for a in release['assets'] if a['name'] == 'cloudflared-windows-amd64.exe')
    url = asset['browser_download_url']
    expected = asset.get('digest', '')
    if not url.startswith('https://github.com/cloudflare/cloudflared/releases/download/') or not expected.startswith('sha256:'):
        raise RuntimeError('Не удалось проверить официальный источник и контрольную сумму.')
    folder = Path(__file__).resolve().parent / 'tools'
    folder.mkdir(exist_ok=True)
    path = folder / 'cloudflared.download'
    try:
        with urllib.request.urlopen(url, timeout=60) as response, path.open('wb') as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if 'sha256:' + digest != expected:
            raise RuntimeError('Контрольная сумма файла не совпала.')
        path.replace(folder / 'cloudflared.exe')
        (folder / 'source.json').write_text(json.dumps(dict(version=release['tag_name'], source=url,
            sha256=digest, github_digest_verified=True), indent=2), encoding='utf8')
        print('cloudflared установлен в папку tools. Контрольная сумма проверена.')
    finally:
        path.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
