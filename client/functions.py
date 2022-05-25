import re
import os.path
import subprocess
import subprocess
import os
import re

from .constants import FILESIZE

regex_get_split= re.compile("(?<=').*?(?=')")
regex_md5sum = re.compile(r'([a-f0-9]{32})')

def get_md5sum(path):
    print("[MD5SUM] Generando...")
    #md5sum = str(subprocess.run(["md5sum","--tag", os.path.abspath(path)],capture_output=True).stdout).split("= ")[1].replace("\\n'","")
    if os.path.exists(path):
        string = fr'md5sum "{path}"'
        md5sum = str(subprocess.run(string, capture_output=True).stdout)
        return regex_md5sum.search(md5sum).group()
    raise Exception("[MD5SUM] No se pudo generar el md5sum")

def split_file(input_, output_folder, **kwargs): 
    """
    Divide un archivo en partes de tamaño size.
    input_: path del archivo a dividir
    output_folder: carpeta donde se guardará los archivos divididos
    """
    size=kwargs.get("size",FILESIZE) 
    verbose= "--verbose" if kwargs.get("verbose",True) else ""
    
    # path y formato de salida de los archivos. Ejemplo, con output='folder/video.mp4_' el archivo será guardado en folder/video.mp4_01,...   
    filename= os.path.basename(input_)
    name= filename + "_"
    output= os.path.join(output_folder, name)
            
    string = f'split "{input_}" -b {size} -d {verbose} "{output}"'                     
    completedProcess= subprocess.run(string, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    if completedProcess.returncode==1:
        print("[SPLIT] No se pudo dividir el archivo\n", completedProcess.stderr.decode())  
        exit()           
    print(completedProcess.stdout.decode())  
    return [os.path.basename(regex_get_split.search(text).group()) for text in completedProcess.stdout.decode().split("\n")[:-1]]
  
def compress_file(input_, output_folder, quality=-1, verbose=True)->None:
    """
    Comprime un archivo a ".gz"
    input_: path del archivo comprimir
    output_folder: carpeta donde se guardará el archivo comprimido
    """
   
    filename= os.path.basename(input_)
    name= filename + ".gz"
    output= os.path.join(output_folder, name)
                
    verbose= "--verbose" if verbose else ""        
    # cmd= fr'gzip {verbose} {quality} -c "{input_}" > "{output}"' 
    input_= input_.replace("\\","/")
    output= output.replace("\\","/")
    cmd= fr'gzip -k {verbose} {quality} -c "{input_}" > "{output}"' 
    # cmd= fr'gzip {verbose} {quality} -c {filename} > {name}'       
    # cmd= fr'gzip {verbose} {quality} -c {filename} > folder/video.gz'       
                
    completedProcess= subprocess.run(cmd, shell=True,stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    if completedProcess.returncode==1:
        print(completedProcess.stderr.decode())  
        return False  
    print(completedProcess.stdout.decode() or completedProcess.stderr.decode())     
    return os.path.basename(output)

# filename= os.path.basename(self.input) # video.mp4
# name= filename + "_" # video.mp4_

# output= os.path.join(self.output, name) # folder/video.mp4_