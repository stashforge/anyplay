import subprocess

import pytest

from media_cli.cli.completion import generate_completion, SERVICE_ACTIONS


@pytest.mark.parametrize('shell', ['bash', 'zsh', 'fish'])
def test_service_completion(shell):
    script = generate_completion(shell)
    assert 'service' in script
    for action in SERVICE_ACTIONS:
        assert action in script
    assert 'service run' not in script


def test_bash_service_actions_and_options(tmp_path):
    script = tmp_path / 'completion.bash'
    script.write_text(generate_completion('bash'))
    subprocess.run(['bash', '-n', str(script)], check=True)
    # Execute the installed callback just as Bash does for Tab.
    probe = f'''source "{script}"
COMP_WORDS=(anyplay service "")
COMP_CWORD=2
$(complete -p anyplay | awk '{{print $3}}')
printf '%s\\n' "${{COMPREPLY[@]}}"
'''
    result = subprocess.run(['bash', '-c', probe], capture_output=True, text=True, check=True)
    assert set(SERVICE_ACTIONS) <= set(result.stdout.splitlines())
    assert 'run' not in result.stdout.splitlines()
