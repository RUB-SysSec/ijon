require 'pry'
require 'set'
$traces = {}

raise unless ARGV[0]

  loop do
    Dir.glob("workdir/sync/#{ARGV[0]}/*/queue/*").each do |path|
      if not $traces.include? path
        puts "new trace #{path}"
        trace = `LD_PRELOAD="/data/projects/ijon/tpm_fuzzing/feeder/derand.so /data/projects/ijon/tpm_fuzzing/feeder/feeder.so" timeout 2s ./src/tpm_server < #{path}`
        $traces[path] = trace
      end
    end

    edge_to_count = {}
    edge_to_path = {}

    $traces.each_pair do |path,data|
      #lines = data.lines.select{|l| not l=~/TPM_COMMAND/ and not l=~/PLAT_COMMAND/ }
			lines = data.lines.select{|l| l.include?("\t") and l.include?("EXEC_COMMAND") and not l.include?("TPM_COMMAND") }
      lines.each_cons(2) do |l1,l2|
        l1 = l1.gsub("\t","++++").strip
        l2 = l2.gsub("\t","++++").strip
				l1 = l1[/\d+/]
				l2 = l2[/\d+/]
        edge_to_count[[l1,l2]] ||= 0
        edge_to_count[[l1,l2]] += 1
        edge_to_path[[l1,l2]] ||= Set.new
        edge_to_path[[l1,l2]] << path
      end
    end

    File.open("graph.dot","w") do |f|
      f.puts "digraph a {"
      edge_to_count.each_pair do |(src,dst), cnt|
        ids = edge_to_path[[src,dst]].map{|p| p=~/sync\/(.*)\/queue\/(id:\d+)/; [$1,$2]}
        opts = Set.new(ids.map{|(x,y)| x.gsub("master","m").gsub("slave","")}).to_a.sort.join("/")
        id = "#{opts}_#{ids.first[1]}"
        #f.puts "\"#{src}\" -> \"#{dst}\" [label=\"#{cnt} (#{id})\"];"
        f.puts "\"#{src}\" -> \"#{dst}\" [label=\"#{cnt}\"];"
        #f.puts "\"#{src}\" -> \"#{dst}\";"
      end
      f.puts "}"
    end

    system("dot graph.dot -Tsvg > graph.svg")
		exit
    puts "done"
    sleep 5
  end
