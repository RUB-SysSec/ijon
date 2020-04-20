require 'thread'

$workers = 12
$timeout = 12*60*60
$sem = Mutex.new
$configs = ["maze.small.bt","maze.small.bt","maze.big.bt", "maze.big.nobt"]

Thread.abort_on_exception = true

def run_level(i, config)
	$sem.synchronize do
		puts "worker #{i} runs #{level}"
	end
	`tmux kill-session -t maze_worker_#{i} 2>/dev/null`
	`tmux new-session -d -s maze_worker_#{i} sh run_afl.sh #{config}`
	sleep(10)
	start = Time.now
	exitreason = "wtf"
	loop do
		if (Time.now - start) > $timeout
			exitreason ="Timeout"
			break
		end
		if not `tmux ls 2>/dev/null`.include?("maze_worker_#{i}") 
			exitreason ="Terminated"
			break;
		end
		sleep(1)
	end
	`tmux kill-session -t maze_worker_#{i} 2>/dev/null`
	$sem.synchronize do
		puts "worker #{i} finished #{level} (#{exitreason})"
	end
end

def work_loop(i)
	while config = $sem.synchronize{$configs.pop}
		run_level(i, level)
	end
end

def worker(i)
	return Thread.new do 
		work_loop(i)
	end
end

$workers.times.map{|i| worker(i)}.each{|t| t.join}

