#!/usr/bin/env python2.7
"""
EMBL writer for ENA data submission. Note that this implementation is basically 
just the documentation at ftp://ftp.ebi.ac.uk/pub/databases/embl/doc/usrman.txt 
in python form - the implementation could be a lot more efficient!

GFF convertion is based on specifications from https://github.com/The-Sequence-Ontology/Specifications/blob/master/gff3.md
"""

shameless_plug="""
    #############################################################################
    # NBIS 2016 - Sweden                                                        #
    # Authors: Martin Norling, Niclas Jareborg, Jacques Dainat                  #
    # Please visit https://github.com/NBISweden/EMBLmyGFF3 for more information #
    #############################################################################
\n"""

TODO="""
TODO: find list of previous ENA release dates and numbers
TODO: find way to retrieve current release date
TODO: add more reasonable way to add references
TODO: add way to handle mandatory features and feature qualifiers (especially contingent dependencies)
"""

import os
import sys
import gzip
import pprint
import time
import logging
import argparse
import re
import curses.ascii
from Bio import SeqIO, Entrez
from BCBio import GFF
from Bio.SeqFeature import SeqFeature, FeatureLocation, ExactPosition
from modules.feature import Feature
from modules.help import Help

SCRIPT_DIR=os.path.dirname(os.path.abspath(sys.argv[0]))
FEATURE_DIR=SCRIPT_DIR + "/modules/features"
QUALIFIER_DIR=SCRIPT_DIR + "/modules/qualifiers"
CPT_LOCUS_GLB=0

class EMBL( object ):
    """
    The basic structure of an EMBL file is like this:
    
    ID - identification             (begins each entry; 1 per entry)
    AC - accession number           (>=1 per entry)
    PR - project identifier         (0 or 1 per entry)
    DT - date                       (2 per entry)
    DE - description                (>=1 per entry)
    KW - keyword                    (>=1 per entry)
    OS - organism species           (>=1 per entry)
    OC - organism classification    (>=1 per entry)
    OG - organelle                  (0 or 1 per entry)
    RN - reference number           (>=1 per entry)
    RC - reference comment          (>=0 per entry)
    RP - reference positions        (>=1 per entry)
    RX - reference cross-reference  (>=0 per entry)
    RG - reference group            (>=0 per entry) RA,RG => At least one of them is mandatory
    RA - reference author(s)        (>=0 per entry)
    RT - reference title            (>=1 per entry)
    RL - reference location         (>=1 per entry)
    DR - database cross-reference   (>=0 per entry)
    CC - comments or notes          (>=0 per entry)
    AH - assembly header            (0 or 1 per entry)   
    AS - assembly information       (0 or >=1 per entry)
    FH - feature table header       (2 per entry)
    FT - feature table data         (>=2 per entry)    
    XX - spacer line                (many per entry)
    SQ - sequence header            (1 per entry)
    CO - contig/construct line      (0 or >=1 per entry) 
    bb - (blanks) sequence data     (>=1 per entry)
    // - termination line           (ends each entry; 1 per entry)
    
    """
    
    legal_values = {'data_class':{"CON":"Entry constructed from segment entry sequences; if unannotated, annotation may be drawn from segment entries",
                                  "PAT":"Patent",
                                  "EST":"Expressed Sequence Tag",
                                  "GSS":"Genome Survey Sequence",
                                  "HTC":"High Thoughput CDNA sequencing",
                                  "HTG":"High Thoughput Genome sequencing",
                                  "MGA":"Mass Genome Annotation",
                                  "WGS":"Whole Genome Shotgun",
                                  "TSA":"Transcriptome Shotgun Assembly",
                                  "STS":"Sequence Tagged Site",
                                  "STD":"Standard (all entries not classified as above)",
                                 },
                    'taxonomy':{"PHG":"Bacteriophage",
                                "ENV":"Environmental Sample",
                                "FUN":"Fungal",
                                "HUM":"Human",
                                "INV":"Invertebrate",
                                "MAM":"Other Mammal",
                                "VRT":"Other Vertebrate",
                                "MUS":"Mus musculus", 
                                "PLN":"Plant",
                                "PRO":"Prokaryote",
                                "ROD":"Other Rodent",
                                "SYN":"Synthetic",
                                "TGN":"Transgenic",
                                "UNC":"Unclassified",
                                "VRL":"Viral",
                               },
                    'topology':['linear', 'circular'],
                    'molecule_type':["genomic DNA", "genomic RNA", "mRNA", "tRNA", "rRNA", "other RNA", "other DNA", 
                                      "transcribed RNA", "viral cRNA", "unassigned DNA", "unassigned RNA"],
                    'organelle':["chromatophore", "hydrogenosome", "mitochondrion", "nucleomorph", "plastid", 
                                 "mitochondrion:kinetoplast", "plastid:chloroplast", "plastid:apicoplast", 
                                 "plastid:chromoplast", "plastid:cyanelle", "plastid:leucoplast", "plastid:proplastid"],
                    'transl_table':[1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25],
                    }
    
    release_dates = {132:time.strptime("2017-05-27", "%Y-%m-%d"),
                     131:time.strptime("2017-04-03", "%Y-%m-%d"),
                     130:time.strptime("2016-11-13", "%Y-%m-%d"),
                     125:time.strptime("2015-09-23", "%Y-%m-%d"),
                     124:time.strptime("2015-07-01", "%Y-%m-%d"), 
                     123:time.strptime("2015-03-23", "%Y-%m-%d"),
                     122:time.strptime("2014-12-09", "%Y-%m-%d"),
                     121:time.strptime("2014-09-24", "%Y-%m-%d"),
                     120:time.strptime("2014-07-01", "%Y-%m-%d"),
                     119:time.strptime("2014-03-17", "%Y-%m-%d"),
                     118:time.strptime("2013-12-17", "%Y-%m-%d"),
                     117:time.strptime("2013-09-12", "%Y-%m-%d"),
                     116:time.strptime("2013-06-27", "%Y-%m-%d"),
                     114:time.strptime("2012-12-21", "%Y-%m-%d"),
                     }
    
    PREVIOUS_VALUES = {}
    
    spacer = "\nXX"
    termination = "\n//\n"
    
    def __init__(self, record = None, verify = False, guess = True):
        """
        Only sets some basic variables.
        """
        super(EMBL, self).__init__()
        self.record = record
        self.verify = verify
        self.guess = guess
        self.refs = []
        self.dbxref = []
        self.assembly_information = []
        self.construct_information = []
        self.comment = ""
        self.translate = False
    
    def _add_mandatory(self):
        """
        Adds mandatory qualifiers that are not always part of the GFF.
        
        Right now, these are specifically a "source" feature, and that
        all spans of n-characters in the sequence has a "gap" feature
        associated.
        """
        # Make sure that there's at least one source feature
        if not [f for f in self.record.features if f.type == 'source']:
            source_location = FeatureLocation(ExactPosition(0), ExactPosition(len(self.record.seq)))
            source_location.strand = 1
            source_feature = SeqFeature( source_location )
            source_feature.qualifiers["mol_type"] = self.molecule_type
            source_feature.qualifiers["organism"] = self.species
            source_feature.type = "source"
            self.record.features[0:0] = [source_feature]
        
        # Make sure that there's a gap feature for every span of n's
        start = None
        for i, c in enumerate(self.record.seq):
            if c in ['n', 'N']:
                if start == None:
                    start = i
            else:
                if start != None:
                    found = False
                    for f in [f for f in self.record.features if f.type == 'gap']:
                        if f.location.start == start and f.location.end == i:
                            found = True
                    if not found:
                        gap_location = FeatureLocation(ExactPosition(start), ExactPosition(i))
                        gap_location.strand = 1
                        gap_feature = SeqFeature( gap_location )
                        gap_feature.qualifiers["estimated_length"] = i-start
                        gap_feature.type = "gap"
                        self.record.features += [gap_feature]
                    
                start = None
    
    def _get_release(self, date):
        """
        Tries to find the correct release number for a given date.
        
        This currently only uses a hard list of release numbers though,
        so a way of getting all releases directly from ENA would really 
        help!
        
        There is a file called Release_[num] in 
        ftp://ftp.ebi.ac.uk/pub/databases/ena/sequence/release/doc/
        which could help with this, but it's not certain to be
        persistent.
        
        TODO: find way to get latest release number and date!
        """
        
        previous = None
        for release in sorted(self.release_dates):
            if not previous:
                previous = self.release_dates[release]
                continue
            if date > previous and date < self.release_dates[release]:
                return release
        
        return max(self.release_dates.keys())+1
    
    def _multiline(self, prefix, data, sep=";", suffix="", indent = 3, quoted = False):
        """
        Creates a multiline entry.
        
        If data is a list, the entries are listed with "sep" as separator, if data is
        a string, it's quoted over as many lines as needed.
        """
        output=""

        #particular case when RT come empty. We must print ; wihtout quotes
        if(prefix == "RT" and data == ";"):
            output = "%s%s" % (prefix, " "*indent)
            output += str(data)
            return "\n" + output + suffix
        
        def split_string(s, char_count):
            output_s = "\n"
            current_line = "RA   "
            for item in s.split(', '):
                if len(item) + len(current_line) >= char_count-2:
                    output_s += current_line + '\n'
                    current_line = "RA   " + item
                elif current_line == "RA   ":
                    current_line += item
                else:
                    current_line += ', ' + item
            if len(current_line) > 5:
                output_s += current_line
            return output_s + ';'

        if prefix == "RA":
            return split_string(data[0], 75)

        # List Case
        previousChunck=""
        if type(data) == type([]):
            #logging.error("!!!!!!!!!!!!!!!LIST case!!!!!!!!!")
            for i, item in enumerate(data):
                if item:
                    currentChunck = item + previousChunck
                    # If item of the list is too long we have to split it as well
                    if len(currentChunck) > 75:
                        output,lastLine=self._splitStringMultiline(output, currentChunck, quoted, char_count=73)
                        previousChunck="\n"+lastLine
                    else:
                        previousChunck=currentChunck

                    #Now add separator between chuncks
                    if len(previousChunck) > 75 : # >= Because when previousChunck is last line and is 75 char length, adding the \n will give string longer than 75

                        output, lastLine=self._splitStringMultiline(output, previousChunck, quoted, char_count=71)
                        previousChunck="%s " % lastLine + sep
                    else:
                        previousChunck+="%s " % sep

            output+=previousChunck

        # String case
        else:
            output,lastLine=self._splitStringMultiline(output, data, quoted)
            if len(output) == 0: # only one line
                output += lastLine + sep
            else: # assume no string longer than 150
                output += "\n" + lastLine + sep
            #logging.error("!!!!!!!!!!!!!!!String case!!!!!!!!!")
            #output,lastLine=self._splitStringMultiline(output, data, quoted)
            #if len(lastLine) >= 75:
            #    output+=lastLine+"\n"+sep
            #elif len(lastLine) == 0:
            #    output += sep
            #else:    
            #    output+=lastLine+sep

        #Check if we have output. If not we have to avoid the strip at the end
        doNotStrip=False
        if not output: 
            doNotStrip = True
        #Last step: add prefix at each line
        cleanOutput=""
        if output:
            listLine= output.split("\n")
            for i, line in enumerate(listLine):
                if i == 0:
                    cleanOutput += "%s%s" % (prefix, " "*indent) + line
                else:
                    cleanOutput += "\n%s%s" % (prefix, " "*indent) + line
        else:
            cleanOutput += "%s%s" % (prefix, " "*indent) #the "+sep" is a trick to keep the final cleaning within the return working properly

        if doNotStrip: # Because is only i.e >KW    <
            return "\n" + cleanOutput + suffix
        else:
            return "\n" + cleanOutput.strip().strip(sep) + suffix
    
    # This method allow to wrap a sting at a size of 75 taking care of quote
    # It return back the result in different part: the last line and everything before if exists.
    def _splitStringMultiline(self, output, data, quoted, char_count=75):
        lastLine=""
        string = " ".join(data.split("\n"))
        #output += "\"" if quoted else ""
        output += ""

        roundl=0
        while string:
            roundl+=1
            if roundl == 1: #Within the round 1 the indentation already exists
                if quoted: 
                    if len(string) + 2 <= char_count: #peculiar case quotes plus string exactly 75
                        lastLine += "\"" 
                        lastLine = string
                        string = string[len(string):] 
                    else:# len(string) + 1 > 75: # + 1 quote
                        splitLoc = self._splitWordsMax(string,char_count)
                        line = string[:splitLoc]
                        string = string[len(line):]
                        string=string.strip() # remove white space
                        output += "\"" 
                        output +=line
                else:
                    if len(string) <= char_count:
                        lastLine = string
                        string = string[len(string):] 
                    else: # len(string) > 75:
                        splitLoc = self._splitWordsMax(string,char_count)
                        line = string[:splitLoc]
                        string = string[len(line):]
                        string=string.strip() # remove white space
                        output +=line

            else: #Not the first round
                if quoted: 
                    if len(string)+1 > char_count:
                        splitLoc = self._splitWordsMax(string,char_count) 
                        line = string[:splitLoc]
                        string = string[len(line):]
                        string=string.strip() # remove white space
                        output +="\n"+line
                    else: #it the last round
                        lastLine += string
                        string = string[len(string):]
                else:
                    if len(string) > char_count:
                        splitLoc = self._splitWordsMax(string,char_count)
                        line = string[:splitLoc]
                        string = string[len(line):]
                        string=string.strip() # remove white space
                        output +="\n"+line
                    else: #it the last round
                        lastLine +=string
                        string = string[len(string):]

        lastLine += "\"" if quoted else ""

        return output,lastLine

    def _splitWordsMax(self, string, valueMax):
        position=0
        positionBefore=0

        words = string.split()
        newString=words.pop(0)
        position = len(newString)
        if position >= valueMax:
            return valueMax

        while position <= valueMax:
            positionBefore=position
            newString += " "+words.pop(0)
            position = len(newString)

        return positionBefore

    
    def _verify(self, key, key_type):
        """
        Looks through the dictionary of legal values to try to validate header values.
        if an illegal value is found, the user is asked to add a new value, which is 
        the assumed for all other instances of the same key (for multi-record GFFs).
        """
        if key_type in EMBL.PREVIOUS_VALUES:
            return EMBL.PREVIOUS_VALUES[key_type]
        
        if key_type not in self.legal_values:
            sys.stderr.write("Can't verify value for %s, legal values unknown." % key_type)
            return key
        legal_values = self.legal_values[key_type]
        while key not in self.legal_values[key_type]:
            if key == '':
                sys.stderr.write("\nNo value provided for %s.\n" % (key_type))
            else:
                sys.stderr.write("\n'%s' is not a legal value for %s.\n" % (key, key_type))
            sys.stderr.write("Legal values are:\n")
            if type(self.legal_values[key_type]) == type({}):
                for value, description in self.legal_values[key_type].iteritems():
                    sys.stderr.write("  - %s\t%s\n" % (value, description))
            else:
                for value in self.legal_values[key_type]:
                    sys.stderr.write("  - %s\n" % value)
            if key == '':
                sys.stderr.write("Please enter a value: ")
            else:
                sys.stderr.write("Please enter new value: ")
            key = raw_input()
            if key.isdigit(): key = int(key) 
            if key in self.legal_values[key_type]:
                EMBL.PREVIOUS_VALUES[key_type] = key
            
        return key
    
    #if species is a taxid we change by the species name
    def get_species_from_taxid(self, taxid):
        #if it is an integer (a taxid), try to get the species name
        species = taxid
        if taxid.isdigit():
           Entrez.email = EMBL.PREVIOUS_VALUES["email"]
           # fetch the classification sufing the taxid
           logging.info("Fecth The Lineage using Entrez.efetch")
           search = Entrez.efetch(id=taxid, db="taxonomy", retmode="xml")
           data = Entrez.read(search)
           species = data[0]['ScientificName']

        return "%s%s" % (species[0].upper(), species[1:].lower())

    #if species is a taxid we change by the species name
    def get_taxid_from_species(self, species):
        #if it is a species name try to get the taxid
        taxid = species
        if not species.isdigit():
            Entrez.email = EMBL.PREVIOUS_VALUES["email"]
            #fetch taxid from ncbi taxomomy
            logging.info("Fecth the taxid from species name using Entrez.esearch")
            species =  species.replace(" ", "+").strip()
            search = Entrez.esearch(term=species, db="taxonomy", retmode="xml")
            record = Entrez.read(search)
            if not record['IdList']: #no taxid found
                logging.error("Please verify the species name. <%s> species is unknown into the NCBI taxonomy databse. Impossible to check the taxonomic classification. We will use the default value 'Life' to populate the OC line.",self.species)
                taxid=None
            else:
                taxid = record['IdList'][0]

        return taxid

    def add_xref(self, xref):
        """
        adds an external reference to the list.
        """
        self.dbxref += [xref]
    
    def add_reference(self, title, positions = "all", location = "", comment = "", xrefs = [], group = [], authors = []):
        """
        Adds a reference for the data in the file to the header.
        """
        self.refs += [{'title':title,
                       'positions':positions if positions != 'all' else [(1,len(self.record.seq))],
                       'location':location if location else "Submitted (%s) to the INSDC." % (time.strftime("%d-%b-%Y").upper()),
                       'comment':comment,
                       'xrefs':xrefs,
                       'group':group,
                       'authors':authors}]
    
    def ID(self):
        """
        from ftp://ftp.ebi.ac.uk/pub/databases/embl/doc/usrman.txt:
        
        The ID (IDentification) line is always the first line of an entry. The
        format of the ID line is:
        ID   <1>; SV <2>; <3>; <4>; <5>; <6>; <7> BP.
        The tokens represent:
           1. Primary accession number
           2. Sequence version number
           3. Topology: 'circular' or 'linear'
           4. Molecule type (see note 1 below)
           5. Data class (see section 3.1)
           6. Taxonomic division (see section 3.2)
           7. Sequence length (see note 2 below)
        
        """
        
        if self.verify:
            self.topology       = self._verify( self.topology,       "topology")
            self.molecule_type = self._verify( self.molecule_type, "molecule_type")
            self.data_class     = self._verify( self.data_class,     "data_class")
            self.taxonomy       = self._verify( self.taxonomy,       "taxonomy")
        
        return "ID   %s; %s; %s; %s; %s; %s; %i BP." % (self.accession, self.version, self.topology, 
                                                           self.molecule_type, self.data_class, self.taxonomy, 
                                                           len(self.record.seq) ) + self.spacer
    
    def AC(self):
        """
        from ftp://ftp.ebi.ac.uk/pub/databases/embl/doc/usrman.txt:
        
        The AC (ACcession number) line lists the accession numbers associated with 
        the entry. Each accession number, or range of accession numbers, is terminated by a
        semicolon. Where necessary, more than one AC line is used.

        The submission case:
        The entry name is extracted from the AC * line . The entry name must be prefixed with a '_' when using the flat file format. No spaces or pipe character ('|') are allowed in the name.
        Example: AC * _contig1
        """
        
        output = "AC   "
        
        if len(output) + len(self.accession) > 80:
            output += "\nAC   "
        output += self.accession + "; "
        
        #add the AC * _contig1 line
        output += "\nXX"
        description=""
        if hasattr(self.record, "description"):
            description = self.record.id
        else:
            logging.error("The first row is missing within the gff3 file.")
            description = "xxx";
        output += "\nAC * _"+description

        return "\n" + output.strip() + self.spacer
    
    def PR(self):
        """
        The PR (PRoject) line shows the International Nucleotide Sequence Database
        Collaboration (INSDC) Project Identifier that has been assigned to the entry.
        Full details of INSDC Project are available at
        http://www.ebi.ac.uk/ena/about/page.php?page=project_guidelines.
        """
        return "\nPR   Project:%s;" % self.project_id + self.spacer
    
    def DT(self):
        """
        The DT (DaTe) line shows when an entry first appeared in the database and
        when it was last updated.  Each entry contains two DT lines, formatted
        as follows:
        DT   DD-MON-YYYY (Rel. #, Created)
        DT   DD-MON-YYYY (Rel. #, Last updated, Version #)
        
        the Release number (Rel.) indicates the first quarterly release made *after* 
        the entry was created or last updated.
        
        The Release number is in places like the header of ftp://ftp.ebi.ac.uk/pub/databases/embl/doc/usrman.txt
        but I have no idea where to get it most easily... 
        """
        
        updated = time.localtime() # this should be the latest update...
        
        output  = "\nDT   %s (Rel. %s, Created)" % (time.strftime("%d-%b-%Y", self.created), self._get_release(self.created))
        #output += "\nDT   %s (Rel. %s, Last updated, Version %i)" % (time.strftime("%d-%b-%Y", updated), 
        #                                                             self._get_release(updated), self.version)
        return output + self.spacer
    
    def DE(self):
        """
        The DE (Description) lines contain general descriptive information about the
        sequence stored. This may include the designations of genes for which the
        sequence codes, the region of the genome from which it is derived, or other
        information which helps to identify the sequence.
        """
        output = ""
        temp = str(self.description)
        while temp:
            output += "\nDE   %s" % temp[:75]
            temp = temp[75:]
        return output + self.spacer
    
    def KW(self):
        """
        The KW (KeyWord) lines provide information which can be used to generate
        cross-reference indexes of the sequence entries based on functional,
        structural, or other categories deemed important.
        """
        
        output = "KW   "
        
        if len(self.keywords) == 0 and self.verify:
            sys.stderr.write("At least one keyword is needed: ")
            self.keywords += [raw_input()]
        
        return self._multiline("KW", self.keywords, suffix=".") + self.spacer
    
    def OS(self):
        """
        The OS (Organism Species) line specifies the preferred scientific name of
        the organism which was the source of the stored sequence. In most 
        cases this is done by giving the Latin genus and species designations, 
        followed (in parentheses) by the preferred common name in English where
        known. The preferred format is:
             OS   Genus species (name)
        """
        return "\nOS   %s" % self.species + self.spacer
    
    def OC(self):
        """
        The OC (Organism Classification) lines contain the taxonomic classification
        of the source organism as described in Section 2.2 of 
        ftp://ftp.ebi.ac.uk/pub/databases/embl/doc/usrman.txt
        """
        
        output = "OC   "
        
        if len(self.classification) == 0 and self.verify:
            sys.stderr.write("At least one classification level is needed: ")
            self.classification += [raw_input()]
        
        return self._multiline("OC", self.classification, suffix=";") + self.spacer
    
    def OG(self):
        """
        The OG (OrGanelle) linetype indicates the sub-cellular location of non-nuclear
        sequences.  It is only present in entries containing non-nuclear sequences
        and appears after the last OC line in such entries.
        
        The OG line contains
        a) one data item (title cased) from the controlled list detailed under the
        /organelle qualifier definition in the Feature Table Definition document
        that accompanies this release or
        b) a plasmid name.
        Examples include "Mitochondrion", "Plastid:Chloroplast" and "Plasmid pBR322".
        
        legal values from http://www.insdc.org/controlled-vocabulary-organelle-qualifier
        """
        
        if self.organelle:
            return ("OG   %s" % (self.organelle,)).strip() + "\n" + self.spacer
        return ""
    
    def RF(self):
        """
        The Reference (RN, RC, RP, RX, RG, RA, RT, RL) Lines
        These lines comprise the literature citations within the database.
        The citations provide access to the papers from which the data has been 
        abstracted. The reference lines for a given citation occur in a block, and
        are always in the order RN, RC, RP, RX, RG, RA, RT, RL. 
        
        >>>> Within each such reference block <<<<<

        the RN line occurs once, the RC, RP and RX lines occur zero
        or more times, and the RA, RT, RL lines each occur one or more times. 
        If several references are given, there will be a reference block for each.
        """
        
        output = ""
        
        for i, ref in enumerate(self.refs):
            output += "\nRN   [%i]" % (i+1)                         # RN - reference number           (>=1 per entry)
            if ref['comment']:                                      # RC - reference comment          (>=0 per entry)
                output += self._multiline("RC", ref['comment'])
                                                                    # RP - reference positions        (>=1 per entry)
            output += self._multiline("RP", ["%i-%i" % pos for pos in ref['positions']])   
            if ref['xrefs']:
                for xref in ref['xrefs']:                               # RX - reference cross-reference  (>=0 per entry)
                    output += self._multiline("RX", xref, suffix=".")
            if ref['group']:                                        # RG - reference group            (>=0 per entry)
                output += self._multiline("RG", ref['group'])
            if ref['authors']:                                      # RA - reference author(s)        (>=0 per entry)
                output += self._multiline("RA", ref['authors'], sep=", ", suffix=";")
            if ref['title'] == ";":                                 # RT - reference title            (>=1 per entry)
                output += self._multiline("RT", ref['title'], quoted=False)
            else:
                output += self._multiline("RT", ref['title'], suffix=';', quoted=True)
            # TODO: There are lots of recommended formatting for the references,
            #       but I won't bother implementing them right now.
            output += self._multiline("RL", ref['location'])        # RL - reference location         (>=1 per entry)
        
        if output:
            return output + self.spacer
        
        return ""
    
    def DR(self):
        """
        The DR (Database Cross-reference) line cross-references other databases which
        contain information related to the entry in which the DR line appears. For
        example, if an annotated/assembled sequence in ENA is cited in the IMGT/LIGM
        database there will be a DR line pointing to the relevant IMGT/LIGM entry.
        """
        if self.dbxref:
            output = ""
            for xref in self.dbxref:
                output += self._multiline("DR", xref, suffix=".")
            return output + self.spacer
        return ""
    
    def CC(self):
        """
        CC lines are free text comments about the entry, and may be used to convey 
        any sort of information thought to be useful that is unsuitable for
        inclusion in other line types.
        """
        return self._multiline("CC", self.comment, quoted=True) + self.spacer if self.comment else ""
    
    def AH(self):
        """
        Third Party Annotation (TPA) and Transcriptome Shotgun Assembly (TSA) records
        may include information on the composition of their sequences to show
        which spans originated from which contributing primary sequences. The AH
        (Assembly Header) line provides column headings for the assembly information.
        The lines contain no data and may be ignored by computer programs.
        """
        return "AH   LOCAL_SPAN     PRIMARY_IDENTIFIER     PRIMARY_SPAN     COMP"
    
    def AS(self):
        """
        The AS (ASsembly Information) lines provide information on the composition of 
        a TPA or TSA sequence. These lines include information on local sequence spans
        (those spans seen in the sequence of the entry showing the AS lines) plus
        identifiers and base spans of contributing primary sequences (for ENA
        primary entries only).
    
        a) LOCAL_SPAN               base span on local sequence shown in entry  
        b) PRIMARY_IDENTIFIER       acc.version of contributing ENA sequence(s)
                                    or trace identifier for ENA read(s)
        c) PRIMARY_SPAN             base span on contributing ENA primary
                                    sequence or not_available for ENA read(s)
                                   
        d) COMP                     'c' is used to indicate that contributing sequence
                                    originates from complementary strand in primary
                                    entry
        """
        output = ""
        for assembly in self.assembly_information:
            output += "AS   %s%s%s%s" % ("{:16}".format(assembly['local_span']),
                                         "{:24}".format(assembly['identifier']),
                                         "{:18}".format(assembly['primary_span']),
                                         assembly['complementary'])
        return output
    
    def FH(self):
        """
        The FH (Feature Header) lines are present only to improve readability of
        an entry when it is printed or displayed on a terminal screen. The lines 
        contain no data and may be ignored by computer programs. The format of these
        lines is always the same.
        """
        return "\nFH   Key             Location/Qualifiers\nFH"
    
    def FT(self):
        """
        The FT (Feature Table) lines provide a mechanism for the annotation of the
        sequence data. Regions or sites in the sequence which are of interest are
        listed in the table. In general, the features in the feature table represent
        signals or other characteristics reported in the cited references. In some
        cases, ambiguities or features noted in the course of data preparation have 
        been included.  The feature table is subject to expansion or change as more
        becomes known about a given sequence.
        
        Feature Table Definition Document:
        A complete and definitive description of the feature table is given 
        in the document "The DDBJ/ENA/GenBank Feature Table:  Definition". 
        URL: ftp://ftp.ebi.ac.uk/pub/databases/embl/doc/FT_current.txt
        """
        
        output = ""
        locus_tag_prefix="|".join(self.locus_tag if type(self.locus_tag) == type([]) else [self.locus_tag])
        for i, feature in enumerate(self.record.features):
            
            #manage locus_tag
            locus_tag=None
            if feature.type.lower() != "source" and feature.type.lower() != "gap":
                #global CPT_LOCUS_GLB
                #CPT_LOCUS_GLB+=1
                #locus_tag_suffix="locus"+str(CPT_LOCUS_GLB)
                locus_tag_suffix = "{}_{}".format(self.record.id, feature.id.split('_')[1])

                # replace locus_tag_suffix by the value of the locus_tag qualifier if this one exists
                for qualifier in feature.qualifiers:
                    if 'locus_tag' == qualifier.lower():
                        locus_tag_suffix = "%s" % "_".join(feature.qualifiers[qualifier])
                
                # create locus tag from locus_tag_suffix and accession      
                locus_tag = "%s_%s" % (locus_tag_prefix, locus_tag_suffix)
            f = Feature(feature, self.record.seq, locus_tag, self.transl_table, translate=self.translate, feature_definition_dir=FEATURE_DIR, qualifier_definition_dir=QUALIFIER_DIR, level=1, reorder_gene_features = self.interleave_genes, force_unknown_features = self.force_unknown_features, force_uncomplete_features = self.force_uncomplete_features)
            
            if not self.keep_duplicates:
                #Deal with identical CDS/UTR/etc between different isoforms:
                if len(f.sub_features) >= 2: # More than two L2 features, lets check them

                    dictionaryType = {}
                    for feature_l2_obj in f.sub_features:
                        
                        # Parse through subfeatures level3
                        rearrange=None
                        ListIndexToRemove = []
                        for i, feature_l3_obj in enumerate(feature_l2_obj.sub_features):
                            # test will be None if no location match one location already saved in the dictionary
                            test = next((elem for elem in dictionaryType if str(elem) == feature_l3_obj.type+str(feature_l3_obj.location)), None)

                            if test:
                                #logging.error("remove %s" % feature_l3_obj.type)
                                rearrange=True
                                ListIndexToRemove.append(i)
                                
                            else: #it is New
                                #logging.error("Add this location %s" % str(feature_l3_obj.type+feature_l3_obj.location))
                                dictionaryType[feature_l3_obj.type+str(feature_l3_obj.location)]=1
                                
                        #Now remove duplicated features
                        if rearrange:
                            cpt = 0
                            for index in ListIndexToRemove:               
                                del feature_l2_obj.sub_features[index-cpt]
                                cpt+=1

            #Print
            output += str(f)
        
        return output + self.spacer
    
    def CO(self):
        """
        Con(structed) sequences in the CON data classes represent complete
        chromosomes, genomes and other long sequences constructed from segment entries.
        CON data class entries do not contain sequence data per se, but rather the
        assembly information on all accession.versions and sequence locations relevant
        to building the constructed sequence. The assembly information is represented in
        the CO lines.
        """
        
        logging.error("CO lines are not currently implemented.")
    
    def SQ(self, out = None):
        """
        The SQ (SeQuence header) line marks the beginning of the sequence data and 
        Gives a summary of its content.
        
        This is followed by sequence data lines, which have a line code consisting 
        of two blanks. The sequence is written 60 bases per line, in groups of 10 
        bases separated by a blank character, beginning at position 6 of the line. 
        The direction listed is always 5' to 3', and wherever possible the 
        non-coding strand (homologous to the message) has been stored.
        
        This function can be passed a streamhandler to write directly to, instead of 
        buffering the entire sequence.
        """
        seq = str(self.record.seq) if self.record else ""
        num_a = seq.count("a")+seq.count("A")
        num_c = seq.count("c")+seq.count("C")
        num_g = seq.count("g")+seq.count("G")
        num_t = seq.count("t")+seq.count("T")
        num_o = len(seq) - (num_a + num_c + num_g + num_t)
        
        output = "\nSQ   Sequence %i BP; %i A; %i C; %i G; %i T; %i other;" % (len(seq), num_a, num_c, num_g, num_t, num_o)
        
        if out:
            out.write(output)
            output = ""
        
        seq_len = 0
        while seq:
            current_line = " ".join([seq[i*10:(i+1)*10] for i in range(0, 6)])
            seq_len += min(60, len(seq))
            formatted_line = "\n     %s %s" % ("{:65}".format(current_line), "{:>9}".format(str(seq_len)))
            
            if out:
                out.write(formatted_line)
            else:
                output += formatted_line
            seq = seq[60:]
        return output
    
    def set_accession(self, accession = ""):
        """
        Sets the entry accession numbers, or parses it from the current record
        """
        if "accession" in EMBL.PREVIOUS_VALUES:
            self.accession = EMBL.PREVIOUS_VALUES["accession"]
        else:
            if accession:
                self.accession = accession
                EMBL.PREVIOUS_VALUES["accession"] = accession
            elif not hasattr(self, "accession"):
                self.accession = "XXX"
                EMBL.PREVIOUS_VALUES["accession"] = "XXX"
    
    def set_classification(self, classification = []):
        """
        Sets the entry phylogenetic classification, or parses it from the current record
        """
        if "classification" in EMBL.PREVIOUS_VALUES:
            self.classification = EMBL.PREVIOUS_VALUES["classification"]
        else:
            if classification:
                self.classification = classification
                EMBL.PREVIOUS_VALUES["classification"] = classification
            #elif hasattr(self.record, "classification"):
            #    self.classification += self.record.classification
            if not getattr(self, "classification", False):
                lineage = "Life" # default value     
                try:
                    taxid = self.get_taxid_from_species(self.species)
                    
                    if taxid:  
                        # fetch the classification sufing the taxid
                        logging.info("Fecth The Lineage using Entrez.efetch")
                        Entrez.email = EMBL.PREVIOUS_VALUES["email"]
                        search = Entrez.efetch(id=taxid, db="taxonomy", retmode="xml")
                        data = Entrez.read(search)
                        lineage = data[0]['Lineage']
                except IOError as e:
                    logging.error(e)
                    logging.error("<Life> will be used by default to populate the OC line to keep a format suitable for ENA submission")

                self.classification = [lineage]
                EMBL.PREVIOUS_VALUES["classification"] = [lineage]
    
    def set_created(self, timestamp = None):
        """
        Sets the creation time of the original entry.
        """
        if timestamp:
            self.created = timestamp
        elif hasattr(self.record, "created"):
            self.created = self.record.created
        elif not hasattr(self, "created"):
            self.created = time.localtime()
    
    def set_data_class(self, data_class = None):
        """
        Sets the sample data class, or parses it from the current record.
        """
        if "data_class" in EMBL.PREVIOUS_VALUES:
            self.data_class = EMBL.PREVIOUS_VALUES["data_class"]
        else:
            if data_class:
                self.data_class = data_class
                if self.verify:
                    self.data_class = self._verify( self.data_class, "data_class")
            #elif hasattr(self.record, "data_class"):
            #    self.data_class = self.record.data_class
            elif not hasattr(self, "data_class"):
                self.data_class = "XXX"
                EMBL.PREVIOUS_VALUES["data_class"] = "XXX"
    
    def set_description(self, description = None):
        """
        Sets the sample description.
        """
        if description:
            self.description = description
        #elif hasattr(self.record, "description"):
        #    self.description = self.record.description
        #elif not hasattr(self, "description"):
        #    self.description = ""
        elif not hasattr(self, "description"):
            self.description = "XXX"

    def set_email(self, email = None):
        """
        Sets the sample data class, or parses it from the current record.
        """
        if "email" in EMBL.PREVIOUS_VALUES:
            self.email = EMBL.PREVIOUS_VALUES["email"]
        else:
            if email:
                self.email = email
                EMBL.PREVIOUS_VALUES["email"] = email
            elif not hasattr(self, "email"):
                self.email = "EMBLmyGFF3@tool.org"
                EMBL.PREVIOUS_VALUES["email"] = "EMBLmyGFF3@tool.org"

    def set_force_uncomplete_features(self, force_uncomplete_features = False):
        """
        Sets wheather to keep features that do not have all the mandatory qualifiers
        """
        self.force_uncomplete_features = force_uncomplete_features

    def set_force_unknown_features(self, force_unknown_features = False):
        """
        Sets wheather to keep feature types not accepted by EMBL in the output
        """
        self.force_unknown_features = force_unknown_features

    def set_interleave_genes(self, interleave = True):
        """
        Sets wheather to interleave mRNA and CDS subfeatures in gene features
        """
        self.interleave_genes = interleave
    
    def set_keep_duplicates(self, duplicate = False):
        """
        Sets wheather to keep duplicate features during the processing
        """
        self.keep_duplicates = duplicate

    def set_keywords(self, keywords = []):
        """
        Sets the entry keywords, and parses those of the current record
        """
        if keywords:
            self.keywords = keywords
        if hasattr(self.record, "keywords"):
            self.keywords += self.record.keywords
        if not getattr(self, "keywords", False):
            self.keywords = [""]
    
    def set_locus_tag(self, locus_tag = ""):
        """
        Sets the entry locus_tag numbers, or parses it from the current record
        """
        if "locus_tag" in EMBL.PREVIOUS_VALUES:
            self.locus_tag = EMBL.PREVIOUS_VALUES["locus_tag"]
        else:
            if locus_tag:
                self.locus_tag = locus_tag
                EMBL.PREVIOUS_VALUES["locus_tag"] = locus_tag
            elif not hasattr(self, "locus_tag"):
                
                sys.stderr.write("No value provided as locus_tag.\nPlease provide a locus_tag:")
                locus_tag = raw_input()
                if not locus_tag:
                    locus_tag="XXX"

                self.locus_tag = locus_tag
                EMBL.PREVIOUS_VALUES["locus_tag"] = locus_tag

    def set_molecule_type(self, molecule_type = None):
        """
        Sets the sample molecule type, or parses it from the current record.
        """
        if "molecule_type" in EMBL.PREVIOUS_VALUES:
            self.molecule_type = EMBL.PREVIOUS_VALUES["molecule_type"]
        else:
            if molecule_type:
                self.molecule_type = molecule_type
            else:
                self.molecule_type = ""
            if self.verify:
                self.molecule_type = self._verify( self.molecule_type, "molecule_type")
            #elif hasattr(self.record, "molecule_type"):
            #    self.molecule_type = self.record.molecule_type

    def set_organelle(self, organelle = None):
        """
        Sets the sample organelle, or parses it from the current record.
        """
        if organelle:
            self.organelle = organelle
        elif hasattr(self.record, "organelle"):
            self.organelle = self.record.organelle
        elif not hasattr(self, "organelle"):
            self.organelle = ""
        
        if self.verify and self.organelle:
            self.organelle = self._verify( self.organelle, "organelle")
    
    def set_project_id(self, project_id = None):
        """
        Sets the project id, or parses it from the current record
        """
        if "project_id" in EMBL.PREVIOUS_VALUES:
            self.project_id = EMBL.PREVIOUS_VALUES["project_id"]
        else:
            if not project_id:
                sys.stderr.write("No project_id provided.\nPlease provide a project ID:")
                project_id = raw_input()
                if not project_id:
                    project_id = "XXX"
  
            self.project_id = project_id
            EMBL.PREVIOUS_VALUES["project_id"] = project_id
            #elif hasattr(self.record, "project_id"):
            #    self.project_id = self.record.project_id
    
    def set_record(self, record):
        """
        Sets the project record (the original GFF data that is currently being converted).
        """
        self.record = record

    def set_species(self, species = None):
        """
        Sets the species, or parses it from the current record
        """
        if "species" in EMBL.PREVIOUS_VALUES:
            self.species = EMBL.PREVIOUS_VALUES["species"]
        else:
            if species:
                species = self.get_species_from_taxid(species)
                self.species = species
                EMBL.PREVIOUS_VALUES["species"] = species
            #elif hasattr(self.record, "species"):
            #    self.species = self.record.species
            else:
                while not species:
                    sys.stderr.write("No value provided for species.\nPlease provide the scientific name or taxid of the organism:")
                    species = raw_input()

                species = self.get_species_from_taxid(species)
                self.species = species
                EMBL.PREVIOUS_VALUES["species"] = species

    def set_taxonomy(self, taxonomy = None):
        """
        Sets the sample taxonomy, or parses it from the current record.
        """
        if "taxonomy" in EMBL.PREVIOUS_VALUES:
            self.taxonomy = EMBL.PREVIOUS_VALUES["taxonomy"]
        else:
            if taxonomy:
                self.taxonomy = taxonomy
                if self.verify:
                    self.taxonomy = self._verify( self.taxonomy, "taxonomy")
            #elif hasattr(self.record, "taxonomy"):
            #    self.taxonomy = self.record.taxonomy
            elif not hasattr(self, "taxonomy"):
                self.taxonomy = "XXX"
                EMBL.PREVIOUS_VALUES["taxonomy"] = "XXX"

    
    def set_topology(self, topology = None):
        """
        Sets the sample topology, or parses it from the current record.
        """
        if topology:
            self.topology = topology
        #elif hasattr(self.record, "topology"):
        #    self.topology = self.record.topology
        elif not hasattr(self, "topology"):
            self.topology = ""
        
        if self.verify:
            self.topology = self._verify( self.topology,       "topology")

    def set_transl_table(self, transl_table = None):
        """
        Sets the translation table, or parses it from the current record.
        """
        if "transl_table" in self.record.features[0].qualifiers:
            self.transl_table = int(self.record.features[0].qualifiers['transl_table'][0])
        elif transl_table:
            self.transl_table = transl_table
        elif not hasattr(self, "transl_table"):
            self.transl_table = ""
        
        if self.verify:
            self.transl_table = self._verify( self.transl_table, "transl_table")

    def set_translation(self, translate = False):
        """
        Sets flag whether to translate CDS features.
        """
        self.translate = translate
    
    def set_version(self, version = None):
        """
        Sets the release version, or parses it from the current record.
        """
        if version:
            self.version = "SV %i" % version
        #elif hasattr(self.record, "version"):
        #    logging.error("If do not have the atttribute ?")
        #    self.version = self.record.version
        elif not hasattr(self, "version"):
            self.version = "XXX"
    
    def write_all(self, out = sys.stdout):
        """
        Writes all EMBL information to the given buffer (default stdout).
        """
           
        if type(out) == type(""):
            out = open(out, 'w')
        
        # Add missing mandatory features:
        self._add_mandatory()
        
        out.write( self.ID() ) # ID - identification             (begins each entry; 1 per entry)
        out.write( self.AC() ) # AC - accession number           (>=1 per entry)
        out.write( self.PR() ) # PR - project identifier         (0 or 1 per entry)
        out.write( self.DT() ) # DT - date                       (2 per entry)
        out.write( self.DE() ) # DE - description                (>=1 per entry)
        out.write( self.KW() ) # KW - keyword                    (>=1 per entry)
        out.write( self.OS() ) # OS - organism species           (>=1 per entry)
        out.write( self.OC() ) # OC - organism classification    (>=1 per entry)
        out.write( self.OG() ) # OG - organelle                  (0 or 1 per entry)
        out.write( self.RF() ) # References, including RN, RC, RP, RX, RG, RA, RT and RL
        out.write( self.DR() ) # DR - database cross-reference   (>=0 per entry)
        out.write( self.CC() ) # CC - comments or notes          (>=0 per entry)
        if self.assembly_information:
            out.write( self.AH() ) # AH - assembly header            (0 or 1 per entry)
            out.write( self.AS() ) # AS - assembly information       (0 or >=1 per entry)
        
        if self.record and self.record.features:
            out.write( self.FH() ) # FH - feature table header       (2 per entry)
        out.write( self.FT() ) # FT - feature table data         (>=2 per entry)
        if self.construct_information:
            out.write( self.CO() )
        
        self.SQ( out )         # SQ - sequence header            (1 per entry)
                               # + sequence...
        
        out.write( self.termination ) # // - termination line    (ends each entry; 1 per entry)
        
        logging.info("Wrote %i CDS features, where %i is sound" % (Feature.CDS_COUNTER, Feature.OK_COUNTER))
        
        # CO - contig/construct line      (0 or >=1 per entry) 
    
##########################
#        MAIN            #
##########################

if __name__ == '__main__':

    # Deal with the advanced help
    if len(sys.argv) > 1 and (sys.argv[1] == "--ah" or sys.argv[1] == "--advanced_help"):
        if len(sys.argv) == 3:
            param = sys.argv[2]
            pattern = re.compile("^--")
            if pattern.search(param):
                param = param[2:]
            sys.stderr.write("advanced help for option "+Help(param))
        elif len(sys.argv) == 2:
            sys.stderr.write("No option specified for the advanced_help, we will display everything:\n")
            sys.stderr.write(Help("all"))
        sys.exit()

    parser = argparse.ArgumentParser( description = __doc__ )
    
    parser.add_argument("gff_file", help="Input gff-file.")
    parser.add_argument("fasta", help="Input fasta sequence.")
    parser.add_argument("-a", "--accession", default=None, help="Accession number(s) for the entry.")
    parser.add_argument("-c", "--created", default=None, help="Creation time of the original entry.")
    parser.add_argument("-d", "--data_class", default=None, help="Data class of the sample.", choices=["CON", "PAT", "EST", "GSS", "HTC", "HTG", "MGA", "WGS", "TSA", "STS", "STD"])
    parser.add_argument("-g", "--organelle", default=None, help="Sample organelle.")
    parser.add_argument("-i", "--locus_tag", help="Locus tag prefix used for the features.")
    parser.add_argument("-k", "--keyword", default=[], nargs="+", help="Keywords for the entry.")
    parser.add_argument("--description", default=None, help="Description that will go for every gene")
    parser.add_argument("-l", "--classification", help="Organism classification e.g \"Eukaryota; Opisthokonta; Metazoa;\". If not set, will be retrieved online on the NCBI taxonomy DB based on the species name or taxid.")
    parser.add_argument("-m", "--molecule_type", default=None, help="Molecule type of the sample.", choices=["genomic DNA", "genomic RNA", "mRNA", "tRNA", "rRNA", "other RNA", "other DNA", "transcribed RNA", "viral cRNA", "unassigned DNA", "unassigned RNA"])
    parser.add_argument("-o", "--output", default=None, help="Output filename.")
    parser.add_argument("-p", "--project_id", help="Project ID.")
    parser.add_argument("-r", "--transl_table", type=int, default=None, help="Translation table.", choices=[1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25])
    parser.add_argument("-s", "--species", default=None, help="Species, formatted as 'Genus species' or using a taxid.")
    parser.add_argument("-t", "--topology", default=None, help="Sequence topology.", choices=["linear", "circular"])
    
    parser.add_argument("-z", "--gzip", default=False, action="store_true", help="Gzip output file")
    
    parser.add_argument("--rc", default=None, help="Reference Comment.")
    parser.add_argument("--rx", default=None, help="Reference cross-reference.")
    parser.add_argument("--rg", default="XXX", help="Reference Group, the working groups/consortia that produced the record.")
    parser.add_argument("--ra", "--author", nargs="+", default="", help="Author for the reference.")
    parser.add_argument("--rt", default=";", help="Reference Title.")
    parser.add_argument("--rl", default=None, help="Reference publishing location.")
    
    parser.add_argument("--keep_duplicates", action="store_true", help="Do not remove duplicate features during the process.")
    parser.add_argument("--interleave_genes", action="store_false", help="Print gene features with interleaved mRNA and CDS features.")
    parser.add_argument("--force_unknown_features", action="store_true", help="Force to keep feature types not accepted by EMBL. /!\ Option not suitable for submission purpose.")
    parser.add_argument("--force_uncomplete_features", action="store_true", help="Force to keep features whithout all the mandatory qualifiers. /!\ Option not suitable for submission purpose.")

    parser.add_argument("--email", default=None, help="Email used to fetch information from NCBI taxonomy database.")
    parser.add_argument("--shame", action="store_true", help="Suppress the shameless plug.")
    parser.add_argument("--translate", action="store_true", help="Include translation in CDS features.")
    
    parser.add_argument("--version", default=None, type=int, help="Sequence version number.")
    parser.add_argument("-x", "--taxonomy", default=None, help="Source taxonomy.", choices=["PHG", "ENV", "FUN", "HUM", "INV", "MAM", "VRT", "MUS", "PLN", "PRO", "ROD", "SYN", "TGN", "UNC", "VRL"])
    
    parser.add_argument("-v", "--verbose", action="count", default=2, help="Increase verbosity.")
    parser.add_argument("-q", "--quiet", action="count", default=0, help="Decrease verbosity.")
    
    parser.add_argument("--ah", "--advanced_help", choices=["One of the parameters above"], help="It display advanced information of the parameter specified. If you don't specify any parameter it will display advanced information of all of them.")

    args = parser.parse_args()
    
    logging.basicConfig(format='%(asctime)s %(levelname)s %(module)s: %(message)s', 
                        level = (5-args.verbose+args.quiet)*10, 
                        datefmt="%H:%M:%S")

    if args.output:
        outfile = args.output
        if args.gzip:
            if not outfile.endswith(".embl.gz"):
                outfile += ".gz" if outfile.endswith(".embl") else ".embl.gz"
            outfile = gzip.open(outfile, "wb")
        else:
            if not outfile.endswith(".embl"):
                outfile += ".embl"
            outfile = open(outfile, "wb")
    else:
        outfile = sys.stdout
    
    infile = gzip.open(args.gff_file) if args.gff_file.endswith(".gz") else open(args.gff_file)
    infasta = gzip.open(args.fasta) if args.fasta.endswith(".gz") else open(args.fasta)
    seq_dict = SeqIO.to_dict( SeqIO.parse(infasta, "fasta") )
    
    if not args.shame:
        sys.stderr.write(shameless_plug)

    for record in GFF.parse(infile, base_dict=seq_dict):
        writer = EMBL( record, True )

        #To set up first
        writer.set_email(args.email) # has to be before set_species
        writer.set_species( args.species ) # has to be before set_classification   
        #Set up the rest
        writer.set_accession( args.accession )
        writer.set_classification( args.classification )
        writer.set_created( args.created )
        writer.set_data_class( args.data_class )
        writer.set_description( args.description )  
        writer.set_force_uncomplete_features( args.force_uncomplete_features )
        writer.set_force_unknown_features( args.force_unknown_features )
        writer.set_interleave_genes( args.interleave_genes )
        writer.set_keep_duplicates( args.keep_duplicates )
        writer.set_keywords( args.keyword )
        writer.set_locus_tag( args.locus_tag )
        writer.set_molecule_type( args.molecule_type )
        writer.set_organelle( args.organelle )
        writer.set_project_id( args.project_id )  
        writer.set_taxonomy( args.taxonomy )
        writer.set_topology( args.topology )
        writer.set_transl_table( args.transl_table )
        writer.set_translation(args.translate)
        writer.set_version( args.version )     

        writer.add_reference(args.rt, location = args.rl, comment = args.rc, xrefs = args.rx, group = args.rg, authors = args.ra)

        writer.write_all( outfile )
     
    sys.stderr.write( """Conversion done\n""")
