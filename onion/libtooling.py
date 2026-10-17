"""Run the Clang 18 source extractor for every selected manifest compilation.

The raw source-extraction document is distinct from T1 execution_structure.
This module does not resolve cross-TU calls or infer runtime observations.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile

from .build_inputs import (ENVIRONMENT, command, compiler_record, digest, file_record,
                           run, validate_entry, validation_args)


WARNINGS = ['-Werror=unknown-warning-option', '-Werror=unused-command-line-argument',
            '-Werror=ignored-optimization-argument']


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')


def _check_manifest(manifest):
    if manifest.get('kind') != 'build_input_manifest' or manifest.get('schema_version') != 1:
        raise ValueError('expected T2 build_input_manifest v1')
    payload = {k: v for k, v in manifest.items() if k != 'manifest_id'}
    if manifest.get('manifest_id') != digest(payload):
        raise ValueError('manifest_id does not match content')
    context = manifest['context']
    for record in context['inputs'] + [context['collector'], context['cmake_tool']]:
        if file_record(record['path'])['sha256'] != record['sha256']:
            raise ValueError('manifest context input changed: ' + record['path'])
    if context['environment'] != {key: os.environ.get(key) for key in ENVIRONMENT}:
        raise ValueError('manifest environment changed; capture inputs again')


def _check_entry(row, context):
    directory, source = Path(row['directory']), Path(row['source'])
    args = command(row['raw'])
    if args != row['arguments']:
        raise ValueError('manifest arguments disagree with compile DB record')
    compiler = compiler_record(args[0], directory)
    if compiler != row['compiler'] or 'clang version 18.' not in compiler['version']:
        raise ValueError('extractor requires the recorded direct Clang 18 compiler')
    dependencies, diagnostics, validation = validate_entry(args, directory, compiler, source)
    if dependencies != row['dependencies'] or validation != row['validation']:
        raise ValueError('source/header/primary input changed; capture inputs again')
    key = digest({'context': digest(context), 'source': row['source'],
                  'directory': row['directory'], 'arguments': args, 'compiler': compiler,
                  'dependencies': dependencies, 'target': row['target'], 'validation': validation})
    if key != row['cache_key']:
        raise ValueError('compilation cache_key mismatch')
    return args, diagnostics


def extract_manifest(manifest, tool, out):
    """Keep one result per entry, including failures, exclusions and variants.

    Revalidate the recorded dependencies before and after tooling. Compile DBs
    are trusted inputs; compiler/plugin/response-file restrictions are T2's.
    No persistent extraction cache is used.
    """
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    _check_manifest(manifest)
    tool = Path(tool).resolve()
    tool_record = file_record(tool)
    entries = []
    for ordinal, row in enumerate(manifest['entries']):
        result = {'manifest_entry': ordinal, 'compilation_id': row.get('cache_key'),
                  'source': row.get('source'), 'target': row.get('target'),
                  'status': row['status'], 'reason': row.get('reason')}
        entries.append(result)
        if row['status'] != 'validated':
            continue
        try:
            args, diagnostics = _check_entry(row, manifest['context'])
            resource = run([row['compiler']['path'], '-print-resource-dir'], row['directory']).stdout.strip()
            # Tooling needs the recorded driver's builtin headers even when the
            # standalone tool lives outside Clang's install tree.
            flags = validation_args(args)
            additions = WARNINGS[:]
            if not any(a == '-resource-dir' or a.startswith('-resource-dir=') for a in flags):
                additions.append('-resource-dir=' + resource)
            tooling_args = [args[0], *flags, *additions]
            result.update(original_arguments=args, tooling_arguments=tooling_args,
                          adjustments={'removed': 'compile/dependency output switches only',
                                       'added': additions, 'tooling': 'ClangTool syntax-only/output adjusters'},
                          validation_diagnostics=diagnostics)
            raw_path = out / f'entry-{ordinal}.json'
            # Never consume old output left by a previous failed invocation.
            raw_path.unlink(missing_ok=True)
            with tempfile.TemporaryDirectory(prefix='onion-tooling-') as temporary:
                write(Path(temporary) / 'compile_commands.json', [
                    {'directory': row['directory'], 'file': row['source'], 'arguments': tooling_args}])
                invocation = [str(tool), '-p', temporary, '--compilation-id=' + row['cache_key'],
                              '--output=' + str(raw_path), row['source']]
                completed = subprocess.run(invocation, text=True, capture_output=True, timeout=180)
            log_path = out / f'entry-{ordinal}.log'
            log_path.write_text(completed.stderr + completed.stdout)
            result.update(log=str(log_path), returncode=completed.returncode)
            if completed.returncode:
                raise ValueError('LibTooling failed; see entry log')
            raw = json.loads(raw_path.read_text())
            validate_extraction(raw)
            if raw['compilation_id'] != row['cache_key'] or 'clang version 18.' not in raw['clang_version']:
                raise ValueError('extractor compilation/version mismatch')
            if not raw['complete']:
                raise ValueError('extractor reported a frontend failure')
            _check_entry(row, manifest['context'])
            result.update(status='extracted', reason=None, artifact=file_record(raw_path),
                          uncertainties=sum(len(f['uncertainties']) for f in raw['functions']))
        except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
            result.update(status='failed', reason=str(error))
    counts = {status: sum(r['status'] == status for r in entries)
              for status in ('extracted', 'excluded', 'failed')}
    result = {'schema_version': 1, 'kind': 'source_extraction_manifest',
              'input_manifest_id': manifest['manifest_id'], 'tool': tool_record,
              'entries': entries, 'summary': counts,
              'ready': counts['failed'] == 0 and counts['extracted'] > 0,
              'scope': 'source CFG and call sites; unresolved calls retained; no execution claims'}
    write(out / 'extraction.json', result)
    write(out / 'failures.json', [r for r in entries if r['status'] == 'failed'])
    return result


def validate_extraction(raw):
    """Check references before handing native output to the local CFG adapter."""
    if (raw.get('kind') != 'clang_source_extraction' or raw.get('schema_version') != 1
            or not raw.get('compilation_id') or raw.get('complete') is not True):
        raise ValueError('expected successful clang_source_extraction v1')
    functions = {f['id']: f for f in raw['functions']}
    if len(functions) != len(raw['functions']):
        raise ValueError('duplicate function ID')
    calls = set()
    for function in functions.values():
        nodes = {n['id']: n for n in function['blocks']}
        if len(nodes) != len(function['blocks']):
            raise ValueError('duplicate block ID')
        if (sum(n['entry'] for n in nodes.values()) != 1 or sum(n['exit'] for n in nodes.values()) != 1):
            if not any(u['kind'] == 'cfg_build_failed' for u in function['uncertainties']):
                raise ValueError('CFG needs exactly one entry and exit')
        for edge in function['edges']:
            if edge['source'] not in nodes or edge['target'] not in nodes:
                raise ValueError('dangling CFG edge')
        for call in function['calls']:
            if call['id'] in calls or call['caller_id'] != function['id']:
                raise ValueError('duplicate call ID or wrong caller')
            calls.add(call['id'])
            if call['block_id'] is not None and call['block_id'] not in nodes:
                raise ValueError('dangling call placement')
            if call['target_id'] is not None and call['target_id'] not in functions:
                raise ValueError('dangling direct callee')
            if call['resolution'] == 'unresolved' and (call['target_id'] is not None or not call['reason']):
                raise ValueError('invalid unresolved call')
    return raw


def build_model(raw, function, sites, build_id, provenance):
    """Adapt one native function to the unchanged function-local Core API.

    Probe markers bind to semantic DeclRefExpr symbols, never printed strings.
    T9 will connect this boundary to the full execution_structure model.
    """
    validate_extraction(raw)
    matches = [f for f in raw['functions'] if function in (f['id'], f['name'], f['qualified_name'])]
    if len(matches) != 1 or not build_id:
        raise ValueError('select exactly one function and provide build_id')
    function = matches[0]
    selected = [s for s in sites if s.get('kind') not in ('lifecycle', 'queue')]
    if len({s['id'] for s in selected}) != len(selected) or len({s['symbol'] for s in selected}) != len(selected):
        raise ValueError('duplicate site IDs or symbols')
    nodes, mapped = [], []
    occurrences = {s['symbol']: [] for s in selected}
    by_symbol = {s['symbol']: s for s in selected}
    for block in function['blocks']:
        node = {'id': block['id'], 'clang_id': block['clang_id'], 'label': block['clang_id'],
                'sites': [], 'entry': block['entry'], 'exit': block['exit'],
                'text': '\n'.join(e.get('text', e['kind']) for e in block['elements']),
                'terminator': copy.deepcopy(block['terminator'])}
        for element in block['elements']:
            symbol = element.get('referenced_symbol')
            if symbol in by_symbol:
                occurrences[symbol].append((node['id'], element['index']))
                node['sites'].append(by_symbol[symbol]['id'])
        nodes.append(node)
    for site in selected:
        hits = occurrences[site['symbol']]
        if len(hits) != 1:
            raise ValueError(f"Site {site['id']} must map exactly once; found {len(hits)}")
        node, index = hits[0]
        mapped.append({**copy.deepcopy(site), 'node': node, 'expression_index': index})
    by_id = {s['id']: s for s in mapped}
    for anchor in ('work.begin', 'work.end'):
        if anchor not in by_id or not by_id[anchor].get('anchor'):
            raise ValueError('required anchor missing or not marked anchor: ' + anchor)
    return {'schema_version': 1, 'build_id': build_id,
            'provenance': {**copy.deepcopy(provenance), 'adapter': 'clang-libtooling-v1',
                           'compilation_id': raw['compilation_id'], 'clang_version': raw['clang_version'],
                           'extraction_digest': digest(raw), 'limitations': raw['limitations']},
            'function': function['name'], 'function_id': function['id'], 'signature': function['type'],
            'nodes': nodes, 'edges': copy.deepcopy(function['edges']),
            'uncertainties': copy.deepcopy(function['uncertainties']),
            'region': {'entry': by_id['work.begin']['node'], 'exit': by_id['work.end']['node']}, 'sites': mapped}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--tool', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = extract_manifest(json.loads(args.manifest.read_text()), args.tool, args.out)
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        args.out.mkdir(parents=True, exist_ok=True)
        write(args.out / 'failures.json', [{'reason': str(error)}])
        write(args.out / 'extraction.json', {'schema_version': 1, 'kind': 'source_extraction_manifest',
                                           'ready': False, 'entries': [], 'failures': [{'reason': str(error)}]})
        return 1
    return 0 if result['ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
