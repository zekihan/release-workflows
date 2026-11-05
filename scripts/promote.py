def promote():
    verify_archive('artifact/image.tar', os.environ['ARCHIVE_SHA256'])
    source = 'oci-archive:artifact/image.tar'
    candidate = inspect_image(source)
    if not candidate:
        raise ValueError('Image archive is empty')
    sha = os.environ['GITHUB_SHA']
    mode = os.environ['MODE']
    version = os.environ['VERSION']
    fingerprint = os.environ['FINGERPRINT']
    if candidate['revision'] != sha or candidate['fingerprint'] != fingerprint:
        raise ValueError('Archive source identity does not match the requested publication')
    validate_platforms(candidate['index'], os.environ['PLATFORMS'])
    names = image_names()
    if mode == 'release':
        stable_version(version)
        if os.environ['GITHUB_REF'] != f'refs/tags/v{version}':
            raise ValueError('Stable publication requires the matching tag')
    elif mode not in ('edge', 'declarative') or os.environ['GITHUB_REF'] != 'refs/heads/main':
        raise ValueError('Invalid publication mode or source branch')

    # Check every destination before mutating either registry.
    edges = [inspect_image(f'docker://{name}:edge') for name in names] if mode != 'release' else []
    for current in edges:
        if current and current['revision']:
            assert_forward(current['revision'], sha, ancestor)
    releases = [inspect_image(f'docker://{name}:{version}') for name in names] if version else []
    if mode == 'release':
        for current in releases:
            immutable_decision(current['digest'] if current else None, candidate['digest'])
    if mode == 'declarative' and any(releases):
        existing = [x for x in releases if x]
        if len({x['digest'] for x in existing}) != 1:
            raise ValueError('Declared release differs between registries')
        # A dependency refresh may change edge without changing the declared release.
        # Recover a missing mirror from the existing immutable digest, not the new build.
        origin = next(i for i, item in enumerate(releases) if item)
        for i, current in enumerate(releases):
            if current is None:
                publish_copy(f'docker://{names[origin]}@{releases[origin]["digest"]}', f'docker://{names[i]}:{version}')
    elif version:
        for name, current in zip(names, releases):
            if current is None:
                publish_copy(source, f'docker://{name}:{version}')

    if version:
        published = [inspect_image(f'docker://{name}:{version}') for name in names]
        if not all(published) or published[0]['digest'] != published[1]['digest']:
            raise ValueError('Release mirrors could not be verified')
        if mode == 'release' and published[0]['digest'] != candidate['digest']:
            raise ValueError('Release verification differs from build')
        for name, release in zip(names, published):
            verify_access(f'docker://{name}:{version}', release['digest'])
        for name, release in zip(names, published) if mode == 'declarative' else []:
            current = inspect_image(f'docker://{name}:latest')
            # Composite upstream versions are ordered by reviewed source history.
            promote_latest = not current or current['digest'] == release['digest']
            if current and current['revision'] and release['revision']:
                promote_latest = ancestor(current['revision'], release['revision'])
            if promote_latest:
                publish_copy(f'docker://{name}@{release["digest"]}', f'docker://{name}:latest')
    if mode != 'release':
        for name in names:
            publish_copy(source, f'docker://{name}:edge')
        for name in names:
            remote = inspect_image(f'docker://{name}:edge')
            if not remote or remote['digest'] != candidate['digest']:
                raise ValueError('Development mirror verification failed')
            verify_access(f'docker://{name}:edge', candidate['digest'])
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as stream:
        stream.write(f'Source: `{sha}`\n\nImage digest: `{candidate["digest"]}`\n')
    output({'digest': candidate['digest']})


promote()
