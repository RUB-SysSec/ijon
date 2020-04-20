require 'pp'
svg = File.read("level7-1.svg")

$paths = {}

workdirs = ARGV

loop do
	paths = []
	workdirs.each do |workdir|
		cur_path = File.read(workdir+"/fuzzer_stats").lines.select{|l| l=~/cur_path/}[0].split(":")[1].strip.to_i
		$stderr.puts "current path: #{cur_path}"

		inputs = Dir.glob(workdir+"/queue/id*")+ Dir.glob(workdir+"/crashes/id*")

		inputs.each do |input|
			next if $paths.include? input
			data = `./build_ijon/smbc_ijon 27 trace < #{input} | uniq`.lines.map{|l| l.strip.split(",").map(&:to_i)}.select{|(x,y)| x != 0 and y != 0}
			$paths[input] =data
			$stderr.puts input
		end

		paths += inputs.map do |input|
			if input=~/id:0*#{cur_path},/
					$stderr.puts "found input"
					color="#f80000"
					alpha = "1"
			elsif input=~/crashes/
					color="#94FF30"
					alpha = "1"
			else
					color="#f8bf00"
					alpha = "0.2"
			end
			data = $paths[input]
			'<path fill="none" stroke="'+color+'" stroke-opacity="'+alpha+'" stroke-width="3" d="M40 176'+data.map{|(x,y)| "L#{x+8} #{y+16}"}.join(" ")+'"><title></title>'+input+'</path>'
		end
	end
	content = svg.gsub("{{PATHS}}", paths.join("\n"))
	File.write("test2.svg", content)
	sleep 0.3
end
