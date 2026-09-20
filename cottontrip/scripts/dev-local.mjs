import {existsSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
const root=fileURLToPath(new URL('../',import.meta.url));
process.chdir(root);
if(existsSync('.env.local'))process.loadEnvFile('.env.local');
const child=spawn(process.execPath,['scripts/run-framework.mjs','dev'],{cwd:root,env:process.env,stdio:'inherit'});
child.on('exit',code=>process.exit(code??1));
child.on('error',()=>{console.error('Local preview could not start.');process.exitCode=1});
