require 'pry'
require 'set'
require 'pp'
$traces = {}
raise unless ARGV[0]
Dir.glob("workdir/sync/#{ARGV[0]}/*/queue/*").each do |path|
  if not $traces.include? path
    #puts "new trace #{path}"
    trace = `LD_PRELOAD="/data/projects/ijon/tpm_fuzzing/feeder/derand.so /data/projects/ijon/tpm_fuzzing/feeder/feeder.so" timeout 2s ./src/tpm_server < #{path}`
    $traces[path] = trace
  end
end

paths = Set.new
$traces.each_pair do |path,data|
  lines = data.lines.select{|l| l.include?("\t") and not l.include?("TPM_COMMAND") }
	cmds = lines.map{|l| l=~/\[\[\[\[\[EXEC_COMMAND (\d+)\]\]\]\]\]/; $1}.join(", ")
	#pp [path,cmds] if !paths.include?(cmds)
  paths << cmds
end

paths.each do |p| pp p end
