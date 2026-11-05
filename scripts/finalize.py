def finalize():
    version = os.environ['VERSION']
    stable_version(version)
    tag = f'v{version}'
    if os.environ['GITHUB_REF'] != f'refs/tags/{tag}':
        raise ValueError('Release publication requires a matching existing tag')
    verify_archive('artifact/manifest.json', os.environ['MANIFEST_SHA256'])
    manifest = json.loads(Path('artifact/manifest.json').read_text())
    for name, checksum in manifest.items():
        if not re.fullmatch(r'[A-Za-z0-9_.+-]+', name) or name in ('.', '..', 'manifest.json'):
            raise ValueError('Unsafe release asset filename')
        verify_archive(f'artifact/{name}', checksum)
    charts = [name for name in manifest if name.endswith('.tgz')]
    for name in charts:
        chart = os.environ['CHART_NAME']
        if name != f'{chart}-{version}.tgz':
            raise ValueError('Unexpected chart name/version')
        destination = f'ghcr.io/{os.environ["GITHUB_REPOSITORY_OWNER"].lower()}/charts'
        existing = command(['skopeo', 'inspect', '--raw', f'docker://{destination}/{chart}:{version}'], missing=True)
        if existing is not None:
            Path('existing-chart').mkdir(exist_ok=True)
            command(['helm', 'pull', f'oci://{destination}/{chart}', '--version', version, '--destination', 'existing-chart'])
            verify_archive(f'existing-chart/{name}', manifest[name])
        else:
            command(['helm', 'push', f'artifact/{name}', f'oci://{destination}'])
        Path('verified-chart').mkdir(exist_ok=True)
        command(['helm', 'pull', f'oci://{destination}/{chart}', '--version', version, '--destination', 'verified-chart'])
        verify_archive(f'verified-chart/{name}', manifest[name])
        authenticated = command(['skopeo', 'inspect', '--raw', f'docker://{destination}/{chart}:{version}'])
        anonymous = command(['skopeo', 'inspect', '--no-creds', '--raw', f'docker://{destination}/{chart}:{version}'])
        if authenticated != anonymous:
            raise ValueError('Anonymous chart access did not match authenticated verification')
    if os.environ['IMAGE']:
        for name in image_names():
            release = inspect_image(f'docker://{name}:{version}')
            if not release or release['digest'] != os.environ['IMAGE_DIGEST']:
                raise ValueError('Released image does not match the verified image job')
        promotions = []
        for name in image_names():
            current = inspect_image(f'docker://{name}:latest')
            previous = current_release_version(current)
            if previous == version:
                immutable_decision(current['digest'], os.environ['IMAGE_DIGEST'])
            if latest_decision(previous, version):
                promotions.append(name)
        for name in promotions:
            publish_copy(f'docker://{name}@{os.environ["IMAGE_DIGEST"]}', f'docker://{name}:latest')
            if inspect_image(f'docker://{name}:latest')['digest'] != os.environ['IMAGE_DIGEST']:
                raise ValueError('Latest verification failed')
    if os.environ['GITHUB_RELEASE'] != 'true':
        return
    existing = subprocess.run(['gh', 'release', 'view', tag, '--json', 'tagName'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assets = [name for name in manifest if name != 'CHANGELOG.md']
    if existing.returncode == 0:
        # Never replace assets of an already published release on rerun.
        if assets:
            Path('existing-release').mkdir(exist_ok=True)
            command(['gh', 'release', 'download', tag, '--dir', 'existing-release'])
            for name in assets:
                verify_archive(f'existing-release/{name}', manifest[name])
        print('Release already exists with matching assets')
        return
    releases = json.loads(command(['gh', 'release', 'list', '--limit', '100', '--json', 'tagName,isPrerelease,isDraft']))
    stable = [stable_version(item['tagName'].removeprefix('v')) for item in releases
              if not item['isPrerelease'] and not item['isDraft']]
    newest = not stable or stable_version(version) > max(stable)
    args = ['gh', 'release', 'create', tag, '--verify-tag', '--title', tag, f'--latest={str(newest).lower()}']
    if 'CHANGELOG.md' in manifest:
        args += ['--notes-file', 'artifact/CHANGELOG.md']
    else:
        args += ['--generate-notes']
    command(args + ['--'] + [f'artifact/{name}' for name in assets])


finalize()
