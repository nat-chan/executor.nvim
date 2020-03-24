import pathlib
import subprocess
import tempfile
import traceback
from datetime import datetime
from subprocess import PIPE
from typing import Any, List, Tuple

import jupyter_core.paths
import jupytext
import nbconvert
import pynvim
from jupyter_client import BlockingKernelClient
from jupyter_client.manager import KernelManager, start_new_kernel
from nbconvert.preprocessors.execute import ExecutePreprocessor, executenb
from nbformat import read as nbread
from nbformat.notebooknode import NotebookNode
from pynvim import Nvim
from pynvim.api.buffer import Buffer


@pynvim.plugin
class Executor(object):
    def __init__(self, nvim: Nvim) -> None:
        self.nvim = nvim
#        self.html_path = tempfile.mktemp(suffix=".html")
        self.html_path = '/tmp/executor.html'
        self.project_root = pathlib.Path(__file__).parent/('../'*3)
        self.executor = None
        self.html_exporter = nbconvert.exporters.HTMLExporter()

    def print(self, txt: Any) -> None:
        txt = str(txt)
        self.nvim.command('new')
        for line in txt.splitlines():
            self.nvim.current.buffer.append(line)

    @staticmethod
    def parse_traceback(line: str) -> Tuple[str,int,str]:
        f = line.lstrip('\033[0;32m').split('\033')[0]
        l = int(line.split('-> ')[-1].split('\033')[0])
        m = line.split('in \033[0;36m')[-1].split('\033')[0]
        return f, l, m

    @staticmethod
    def code_surjection(buffer: Buffer, nb: NotebookNode) -> List[Tuple[int,int]]:
        surjection = [(-1,-1)]*len(buffer)
        i = 0
        for c, cell in enumerate(nb.cells):
            for l, line in enumerate(cell.source.splitlines()):
                while line != buffer[i]:
                    i += 1
                surjection[i] = (c, l)
        return surjection

    @pynvim.command('JNConnect')
    def JKConnect(self) -> None:
        runtime_dir = pathlib.Path(jupyter_core.paths.jupyter_runtime_dir())
        connection_files = runtime_dir.glob("kernel-*.json")
        source = '\n'.join(
            connection_file.name.lstrip('kernel-').rstrip('.json') + ' ' +
            datetime.fromtimestamp(connection_file.stat().st_ctime).strftime("%m/%d %H:%M")
        for connection_file in connection_files)
        proc = subprocess.run("fzf-tmux|awk '{print $1}'", input=source, stdout=PIPE, shell=True, text=True)
        connection_file = 'kernel-%s.json' % proc.stdout.strip()
        connection_file = runtime_dir.joinpath(connection_file).as_posix()
        kc = BlockingKernelClient()
        try:
            kc.load_connection_file(connection_file)
            kc.execute_interactive('', timeout=1)
        except (TimeoutError, FileNotFoundError):
            self.nvim.command("echoerr 'Selected connection is dead!'")
        else:
            self.executor = ExecutePreprocessor()
            self.executor.kc = kc
            self.nvim.command("echo 'Successfully connected!'")

    @pynvim.command('JNRun', sync=False)
    def JNRun(self) -> None:
        current_line = self.nvim.current.line
        buffer = '\n'.join(self.nvim.current.buffer)
        nb = jupytext.reads(buffer, {'extension': '.py'})
        surjection = self.code_surjection(self.nvim.current.buffer, nb)
        current_line = self.nvim.call('line', '.') - 1
        current_cell = surjection[current_line][0]
        if current_cell == -1:
            self.nvim.command('echoerr "Current line is out of cell."')
            return

        # cell execution
        _, nb.cells[current_cell].outputs = self.executor.run_cell(nb.cells[current_cell], current_cell)
        #nb.cells[current_cell], resources = self.executor.preprocess_cell(nb.cells[current_cell], {}, current_cell)

        # error handling
        for output in nb.cells[current_cell].outputs:
            if output.output_type == 'error':
                f, l, m = self.parse_traceback(output.traceback[2])
                f = self.nvim.current.buffer.name
                l = surjection.index((current_cell,l-1))+1
                self.nvim.command('enew|setl nohidden|setl bt=nofile')
                self.nvim.current.buffer.append('  File "%s", line %d, in %s' %(f,l,m))
                for line in output.traceback[3:-1]:
                    f, l, m = self.parse_traceback(line)
                    self.nvim.current.buffer.append('  File "%s", line %d, in %s' %(f,l,m))
                self.nvim.command('compiler python|cbuffer|cw|set hidden')
                break

        # html export
        script, resources = self.html_exporter.from_notebook_node(nb)
        script = script.splitlines()
        script.insert(3,'<script type="text/javascript" src="http://livejs.com/live.js"></script>')
        script = '\n'.join(script)
        with open(self.html_path, "w") as f:
            f.write(script)

    @pynvim.command('JNDevTest', nargs='*', sync=True)
    def JNDevTest(self, args):
        self.nvim.command('UpdateRemotePlugins')
        subprocess.call('tmux split -h "nvim %s"'%(self.project_root/'test/test.py'), shell=True)
