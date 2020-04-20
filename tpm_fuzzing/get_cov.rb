require 'pry'
require 'set'
require 'digest'
$traces = {}

Dir.glob("workdir/sync/*/queue/*").each do |path|
  if not $traces.include? path
    puts "new trace #{path}"
    md5 = Digest::MD5.hexdigest(File.read(path))
    trace = ` LLVM_PROFILE_FILE="coverage/#{md5}.profraw" LD_PRELOAD="/home/me/tpm_fuzzing/feeder/derand.so /home/me/tpm_fuzzing/feeder/feeder.so" timeout 2s ./src/tpm_server_cov < #{path}`
    $traces[path] = trace
  end
end

`llvm-profdata merge -output=coverage/code.profdata coverage/*.profraw`
`llvm-cov show ./src/tpm_server_cov -instr-profile=coverage/code.profdata src/*.c -filename-equivalence -use-color --format html > coverage/coverage.html`
