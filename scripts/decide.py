def previously_failed(sha, image, paths):
    repo = os.environ['GITHUB_REPOSITORY']
    run_id = os.environ['GITHUB_RUN_ID']
    workflow = github(f'repos/{repo}/actions/runs/{run_id}')["workflow_id"]
    page = 1
    while True:
        runs = github(f'repos/{repo}/actions/workflows/{workflow}/runs?head_sha={sha}&status=completed&per_page=100&page={page}')['workflow_runs']
        for run in runs:
            if str(run['id']) == run_id or run['conclusion'] not in ('failure', 'timed_out', 'cancelled', 'startup_failure'):
                continue
            if not paths:
                return True
            jobs_page = 1
            while True:
                jobs = github(f'repos/{repo}/actions/runs/{run["id"]}/jobs?per_page=100&page={jobs_page}')['jobs']
                if any(j['conclusion'] in ('failure', 'timed_out', 'cancelled') and f'({image})' in j['name'] for j in jobs):
                    return True
                if len(jobs) < 100:
                    break
                jobs_page += 1
        if len(runs) < 100:
            return False
        page += 1


def decide():
    ref = os.environ['GITHUB_REF']
    event = os.environ['GITHUB_EVENT_NAME']
    mode = os.environ['MODE']
    sha = os.environ['GITHUB_SHA']
    if mode not in ('edge', 'release', 'declarative'):
        raise ValueError('Unknown publication mode')
    if mode == 'release':
        version = Path(os.environ['VERSION_FILE']).read_text().strip()
        stable_version(version)
        if ref != f'refs/tags/v{version}':
            raise ValueError('Release tag must match VERSION')
        main = command(['git', 'rev-parse', 'origin/main']).decode().strip()
        if not ancestor(sha, main):
            raise ValueError('Release commit is outside main history')
        tags = command(['git', 'tag', '--merged', sha]).decode().splitlines()
        validate_version_history(version, tags)
        output({'build': 'true', 'version': version, 'fingerprint': sha})
        return
    if ref != 'refs/heads/main' or event not in ('schedule', 'workflow_dispatch'):
        raise ValueError('Development publishing is main-only and scheduled/manual')
    image = os.environ['IMAGE']
    paths = json.loads(os.environ.get('INPUT_PATHS', '[]'))
    if paths:
        entries = command(['git', 'ls-tree', '-r', 'HEAD', '--', *paths])
        if not entries:
            raise ValueError('Build input paths selected no files')
        fingerprint = hashlib.sha256(entries).hexdigest()
    else:
        fingerprint = sha
    version = ''
    if mode == 'declarative':
        import shlex
        config = {}
        for line in Path(os.environ['VERSION_FILE']).read_text().splitlines():
            if re.match(r'^(version|revision):', line):
                key, value = line.split(':', 1)
                words = shlex.split(value, comments=True)
                if len(words) != 1:
                    raise ValueError('Expected scalar version/revision')
                config[key] = words[0]
        # Images without an upstream omit revision and release their own X.Y.Z.
        version = release_identity(config['version'], config.get('revision'))
    names = image_names()
    images = []
    try:
        images = [inspect_image(f'docker://{name}:edge') for name in names]
    except RuntimeError:
        print('Registry state could not be verified; not assuming publication succeeded')
    source_matches = len(images) == 2 and all(x and x['fingerprint'] == fingerprint for x in images)
    mirrors_match = bool(source_matches and images[0]['digest'] == images[1]['digest'])
    if source_matches and mode == 'declarative':
        try:
            versions = [inspect_image(f'docker://{name}:{version}') for name in names]
            mirrors_match = bool(mirrors_match and all(versions) and versions[0]['digest'] == versions[1]['digest'])
        except RuntimeError:
            mirrors_match = False
    manual = event == 'workflow_dispatch' or int(os.environ.get('GITHUB_RUN_ATTEMPT', '1')) > 1
    failed = not (manual or mirrors_match) and previously_failed(sha, image, paths)
    decision = build_decision(manual=manual, force=os.environ['FORCE'] == 'true',
                              failed=failed, source_matches=source_matches, mirrors_match=mirrors_match)
    for current in images:
        if current and current['revision']:
            assert_forward(current['revision'], sha, ancestor)
    print(f'{image}: {decision}')
    output({'build': str(decision == 'build').lower(), 'version': version, 'fingerprint': fingerprint})


decide()
