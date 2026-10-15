"""Regenerate reviewed v0.1.2 descriptors; needs grpcio-tools==1.76.0 in /tmp.

Usage: python tools/generate_approval_schema.py /path/to/verified/v0.1.2/source
No live Gateway or native CLI is contacted. Keep original wire fields intact.
"""
import base64
import hashlib
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

from google.protobuf import descriptor_pb2
import grpc_tools

METHODS = {'Health', 'GetSandbox', 'GetSandboxConfig', 'GetDraftPolicy',
           'GetSandboxPolicyStatus', 'ApproveAllDraftChunks', 'RejectDraftChunk'}
EXPECTED_SOURCE_HASHES = {'openshell.proto': '2e31d0faca652b91729f88bd4053447e5d162e74de7dbacdcc06327ed82e5b95', 'sandbox.proto': 'ed3d2650c8d1c4cbb653259c6055adba2dba8a51498ab6a1b2cfc339997ab2b9', 'datamodel.proto': 'e50bd875a760f54b80269c65ee47984cae424fb623b7f59551ba62550c23e70b', 'options.proto': '10c8a99b505047f759951285fdae6068afd267ef672d00a65385a91ee8d027ee'}
source = Path(sys.argv[1])
assert {name: hashlib.sha256((source / 'proto' / name).read_bytes()).hexdigest()
        for name in EXPECTED_SOURCE_HASHES} == EXPECTED_SOURCE_HASHES, 'Unreviewed source schema'
root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as directory:
    target = Path(directory) / 'schema.pb'
    subprocess.run([sys.executable, '-m', 'grpc_tools.protoc', '-I' + str(source / 'proto'),
                    '-I' + str(Path(grpc_tools.__file__).parent / '_proto'),
                    '--include_imports', '--descriptor_set_out=' + str(target),
                    str(source / 'proto/openshell.proto')], check=True)
    descriptor = descriptor_pb2.FileDescriptorSet.FromString(target.read_bytes())
messages, enums = {}, {}
def index(message, prefix):
    name = prefix + '.' + message.name
    messages[name] = message
    for nested in message.nested_type:
        index(nested, name)
    for enum in message.enum_type:
        enums[name + '.' + enum.name] = enum
for file in descriptor.file:
    for message in file.message_type:
        index(message, '.' + file.package)
    for enum in file.enum_type:
        enums['.' + file.package + '.' + enum.name] = enum
service = next(s for f in descriptor.file for s in f.service if s.name == 'OpenShell')
kept = {m.input_type for m in service.method if m.name in METHODS} | {m.output_type for m in service.method if m.name in METHODS}
todo = list(kept)
while todo:
    name = todo.pop()
    if name in messages:
        for field in messages[name].field:
            if field.type_name and field.type_name not in kept:
                kept.add(field.type_name)
                todo.append(field.type_name)
        # Preserve entire nested definitions/map-entry metadata/oneofs.
        for nested in messages[name].nested_type:
            nested_name = name + '.' + nested.name
            if nested_name not in kept:
                kept.add(nested_name)
                todo.append(nested_name)
# Parent types must remain for retained nested definitions.
for name in list(kept):
    for parent in messages:
        if name.startswith(parent + '.'):
            kept.add(parent)

def strip(message):
    if not message.options.map_entry:
        message.ClearField('options')
    for field in message.field:
        field.ClearField('options')
    for nested in message.nested_type:
        strip(nested)
    for enum in message.enum_type:
        enum.ClearField('options')
        for value in enum.value:
            value.ClearField('options')

result = descriptor_pb2.FileDescriptorSet()
for file in descriptor.file:
    selected = result.file.add()
    selected.CopyFrom(file)
    selected.ClearField('source_code_info')
    selected.ClearField('options')
    selected.ClearField('extension')
    selected.ClearField('service')
    original_messages = list(selected.message_type)
    original_enums = list(selected.enum_type)
    selected.ClearField('message_type')
    selected.ClearField('enum_type')
    for message in original_messages:
        if '.' + file.package + '.' + message.name in kept:
            value = selected.message_type.add()
            value.CopyFrom(message)
            strip(value)
    for enum in original_enums:
        if '.' + file.package + '.' + enum.name in kept:
            value = selected.enum_type.add()
            value.CopyFrom(enum)
            value.ClearField('options')
    if file.name == 'openshell.proto':
        value = selected.service.add()
        value.name = 'OpenShell'
        for method in service.method:
            if method.name in METHODS:
                item = value.method.add()
                item.CopyFrom(method)
                item.ClearField('options')
# Keep only nonempty files and dependencies on retained files.
nonempty = [f for f in result.file if f.message_type or f.enum_type or f.service]
result.ClearField('file')
for file in nonempty:
    file.ClearField('public_dependency')
    file.ClearField('weak_dependency')
    dependencies = [d for d in file.dependency if d in {f.name for f in nonempty}]
    file.ClearField('dependency')
    file.dependency.extend(dependencies)
    result.file.add().CopyFrom(file)
data = result.SerializeToString(deterministic=True)
encoded = base64.b85encode(zlib.compress(data)).decode()
hashes = {name: hashlib.sha256((source / 'proto' / name).read_bytes()).hexdigest()
          for name in ('openshell.proto', 'sandbox.proto', 'datamodel.proto', 'options.proto')}
body = '''# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generated wire descriptors from NVIDIA OpenShell v0.1.2 (Apache-2.0).
Regenerate with tools/generate_approval_schema.py. Field definitions are unmodified;
unrelated RPCs/messages, comments and custom authorization options are omitted.
See docs/native-api-v0.1.2.md and docs/third-party/OpenShell-LICENSE.txt.
"""
import base64
import zlib
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

SOURCE_HASHES = %r
SCHEMA_SHA256 = %r
_DATA = (
%s
)
DESCRIPTORS = descriptor_pb2.FileDescriptorSet.FromString(zlib.decompress(base64.b85decode(_DATA)))
_POOL = descriptor_pool.DescriptorPool()
for _file in DESCRIPTORS.file:
    _POOL.Add(_file)
_SERVICE = _POOL.FindServiceByName('openshell.v1.OpenShell')
METHODS = {method.name: (message_factory.GetMessageClass(method.input_type),
                         message_factory.GetMessageClass(method.output_type))
           for method in _SERVICE.methods}


def message(type_name, **fields):
    return message_factory.GetMessageClass(_POOL.FindMessageTypeByName(type_name))(**fields)
''' % (hashes, hashlib.sha256(data).hexdigest(), '\n'.join('    ' + repr(encoded[i:i+100]) for i in range(0, len(encoded), 100)))
(root / 'src/openshell_manager/approval/schema.py').write_text(body)
print('Generated descriptor:', len(data), 'bytes;', len(encoded), 'encoded bytes;', len(kept), 'types')
