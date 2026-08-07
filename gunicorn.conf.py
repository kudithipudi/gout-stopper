bind = "unix:/var/www/gout-stopper/gout-stopper.sock"
workers = 1
worker_class = "uvicorn.workers.UvicornWorker"
chdir = "/var/www/gout-stopper"
accesslog = "-"
errorlog = "-"
