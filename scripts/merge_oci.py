"""Combine native build archives without executing image or repository code."""
import tarfile
import tempfile
import shutil


def merge_archives(sources, destination):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        blobs = root / 'blobs/sha256'
        blobs.mkdir(parents=True)
        manifests = []
        for archive, expected in sources:
            verify_archive(archive, expected)
            index = None
            with tarfile.open(archive) as source:
                for member in source:
                    name = member.name.removeprefix('./')
                    if member.isdir() and name in ('.', 'blobs', 'blobs/sha256'):
                        continue
                    if not member.isfile():
                        raise ValueError('Unexpected OCI archive member')
                    if name == 'index.json':
                        index = json.load(source.extractfile(member))
                    elif name == 'oci-layout':
                        if json.load(source.extractfile(member)) != {'imageLayoutVersion': '1.0.0'}:
                            raise ValueError('Unsupported OCI layout')
                    elif re.fullmatch(r'blobs/sha256/[0-9a-f]{64}', name):
                        target = root / name
                        with source.extractfile(member) as src, target.open('wb') as dst:
                            shutil.copyfileobj(src, dst)
                        verify_archive(target, target.name)
                    else:
                        raise ValueError('Unsafe OCI archive path')
            if index is None or len(index['manifests']) != 1:
                raise ValueError('Expected one image per native archive')
            descriptor = index['manifests'][0]
            digest = descriptor['digest']
            if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
                raise ValueError('Invalid OCI digest')
            built = json.loads((blobs / digest.split(':')[1]).read_text())
            if 'manifests' not in built:
                raise ValueError('Expected BuildKit image index')
            manifests.extend(built['manifests'])
        image_index = {'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.index.v1+json', 'manifests': manifests}
        validate_platforms(image_index, 'linux/amd64,linux/arm64')
        data = json.dumps(image_index, separators=(',', ':')).encode()
        digest = hashlib.sha256(data).hexdigest()
        (blobs / digest).write_bytes(data)
        (root / 'index.json').write_text(json.dumps({'schemaVersion': 2, 'manifests': [{
            'mediaType': image_index['mediaType'], 'digest': f'sha256:{digest}', 'size': len(data)}]}))
        (root / 'oci-layout').write_text('{"imageLayoutVersion":"1.0.0"}')
        with tarfile.open(destination, 'w') as archive:
            for path in sorted(root.rglob('*')):
                info = archive.gettarinfo(path, arcname=str(path.relative_to(root)))
                info.mtime = 0
                info.uid = os.getuid()
                info.gid = os.getgid()
                info.uname = info.gname = ''
                if path.is_file():
                    with path.open('rb') as stream:
                        archive.addfile(info, stream)
                else:
                    archive.addfile(info)


if __name__ == '__main__':
    merge_archives([('artifact/image.tar', os.environ['AMD_SHA256']),
                    ('arm-artifact/image.tar', os.environ['ARM_SHA256'])], 'artifact/combined.tar')
    Path('artifact/combined.tar').replace('artifact/image.tar')
    with Path('artifact/image.tar').open('rb') as stream:
        output({'sha256': hashlib.file_digest(stream, 'sha256').hexdigest()})
