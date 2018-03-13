from __future__ import print_function
import os,sys,glob,time,collections,gc,calendar,weakref,resource,datetime
from netCDF4 import Dataset,netcdftime,num2date
import dimarray as da
import numpy as np
import matplotlib as mpl
mpl.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns
import scipy.ndimage as ndimage
import cv2
from skimage.feature import peak_local_max
import cartopy.crs as ccrs
import cartopy
sns.set_palette(sns.color_palette("plasma"))

sys.path.append('/Users/peterpfleiderer/Documents/Projects/tropical_cyclones/tc_detection')
from TC_support import * ; reload(sys.modules['TC_support'])

class tc_tracks(object):
    def __init__(self,VO,Wind10,MSLP,MSLP_smoothed,SST,T,lats,lons,time_,dates,identifier,working_dir,time_steps=None):
        self._identifier=identifier
        self._working_dir=working_dir
        if os.path.isdir(working_dir)==False:
            os.system('mkdir '+working_dir)
        if os.path.isdir(working_dir+'/track_surrounding')==False:
            os.system('mkdir '+working_dir+'/track_surrounding')
        if os.path.isdir(working_dir+'/track_path')==False:
            os.system('mkdir '+working_dir+'/track_path')
        if os.path.isdir(working_dir+'/track_evolution')==False:
            os.system('mkdir '+working_dir+'/track_evolution')

        # input fields
        self._lats=lats
        self._lons=lons

        self._VO=VO
        self._Wind10=Wind10
        self._MSLP=MSLP
        if MSLP_smoothed is not None:
            self._MSLP_smoothed=MSLP_smoothed
        else:
            self._MSLP_smoothed=MSLP

        self._time=time_
        if time_steps is None:
            time_steps=range(len(self._time))
        self._time_i=time_steps
        self._dates=dates
        self._yr_frac=np.array([toYearFraction(dd) for dd in self._dates])

        self._T=T
        self._SST=SST

        # tc cat dict
        self._obs_tc=False
        self._cat_colors={0:'lightblue',1:'#ffffcc',2:'#ffe775',3:'#ffc148',4:'#ff8f20',5:'#ff6060'}
        self._cat_names={0:'tropical storm',1:'Category 1',2:'Category 2',3:'Category 3',4:'Category 4',5:'Category 5'}

    def init_map(self,ax,transform):
        self._ax=ax
        self._transform=transform

        # for storm in range(len(self._tc_sel.storm)):
        #     self._m.plot(self._tc_lon[storm,:],self._tc_lat[storm,:],color='gray')

    def set_thresholds(self,thr_wind,thr_mslp,p_radius,neighborhood_size,warm_core_size,cores_distance,search_radius,min_time_steps):
        self._thr_wind=thr_wind
        self._thr_mslp=thr_mslp
        self._min_time_steps=min_time_steps

        self._p_radius=self.degree_to_step(p_radius)
        self._neighborhood_size=self.degree_to_step(neighborhood_size)
        self._warm_core_size=self.degree_to_step(warm_core_size)**2
        self._cores_distance=self.degree_to_step(cores_distance)
        self._search_radius=self.degree_to_step(search_radius)

    def degree_to_step(self,degree):
        y_step=abs(np.diff(self._lats[:,0],1).mean())
        x_step=abs(np.diff(self._lons[0,:],1).mean())
        return round(degree/(y_step+x_step)*2)

    def tc_cat(self,z,method='pressure'):
        def cat__(zz):
            if method=='wind':
                if zz<=64: cat= 0
                if zz>64: cat= 1
                if zz>82: cat= 2
                if zz>95: cat= 3
                if zz>112: cat= 4
                if zz>136: cat= 5
                if np.isnan(zz): cat= 0
                return cat
            if method=='pressure':
                if zz>=1020: cat= 0
                if zz<1020: cat= 1
                if zz<980: cat= 2
                if zz<965: cat= 3
                if zz<945: cat= 4
                if zz<920: cat= 5
                if np.isnan(zz): cat= 0
                return cat
        if isinstance(z,np.ndarray) or isinstance(z,list) or isinstance(z,da.core.dimarraycls.DimArray):
            return [cat__(zz) for zz in z]
        else:
            return cat__(z)

    # treating ibtracks
    def init_obs_tcs(self,tc_sel):
        self._tc_sel=tc_sel
        tmp_time=tc_sel['source_time'].values
        self._tc_time=tmp_time.copy()*np.nan
        for i in range(tmp_time.shape[0]):
            for j in range(tmp_time.shape[1]):
                if np.isfinite(tmp_time[i,j]):
                    self._tc_time[i,j]=toYearFraction(num2date(tmp_time[i,j],units = 'days since 1858-11-17 00:00:00'))
        self._tc_lat=tc_sel['lat_for_mapping'].values
        self._tc_lon=tc_sel['lon_for_mapping'].values
        self._tc_lon[self._tc_lon>180]-=360
        self._tc_intens=np.nanmean(tc_sel['source_wind'],axis=-1)
        self._obs_tc=True

    def obs_track_info(self,overwrite=False):
        out_file=self._working_dir+'obs_track_info.nc'
        if overwrite and os.path.isfile(out_file):
            os.system('rm '+out_file)
        elif overwrite==False and os.path.isfile(out_file):
            self._obs_track_info=da.read_nc(out_file)
            return self._obs_track_info


        obs_summary=np.zeros([len(self._tc_sel.storm),200,7])*np.nan
        for i,storm in enumerate(self._tc_sel.storm):
            tmp_t=self._tc_time[i,:]
            last_val=len(np.where(np.isfinite(tmp_t))[0])
            obs_summary[i,0:last_val,0]=[self.tc_cat(z,method='wind') for z in np.nanmean(self._tc_sel['source_wind'].values[i,0:last_val,:],axis=-1)]

            for t in range(last_val):
                t_=np.where(abs(self._yr_frac-self._tc_time[i,t])<0.0004)[0]
                if len(t_)!=0:
                    t_=t_[0]
                    y,x=np.argmin(abs(self._lats[:,0]-self._tc_lat[i,t])),np.argmin(abs(self._lons[0,:]-self._tc_lon[i,t]))
                    box_1=[int(bb) for bb in self.get_box(y,x,self._win1)]
                    box_2=[int(bb) for bb in self.get_box(y,x,self._win2)]
                    obs_summary[i,t,1]=self._VO[t_,box_1[0]:box_1[1],box_1[2]:box_1[3]].max()
                    obs_summary[i,t,2]=self._MSLP[t_,box_1[0]:box_1[1],box_1[2]:box_1[3]].min()
                    obs_summary[i,t,3]=self._Wind10[t_,box_2[0]:box_2[1],box_2[2]:box_2[3]].max()
                    obs_summary[i,t,4]=self._T[t_,0,y,x]
                    obs_summary[i,t,5]=self._T[t_,1,y,x]
                    if self._SST is not None:
                        obs_summary[i,t,6]=self._SST[t_,y,x]

        obs_summary=obs_summary[:,np.isfinite(np.nanmean(obs_summary,axis=(0,-1))),:]
        self._obs_track_info=da.DimArray(obs_summary,axes=[self._tc_sel.storm,range(obs_summary.shape[1]),['cat','VO','MSLP','Wind10','T850','T500','SST']],dims=['storm','time','variable'])

        da.Dataset({'obs_track_info':self._obs_track_info}).write_nc(out_file)

        # print summary
        sys.stdout.write('Category:\t0\t\t1\t\t2\t\t3\t\t4\t\t5'); sys.stdout.flush()
        for vari,name in zip(range(2,7),['VO','MSLP','Wind10','T850','T500','SST']):
            sys.stdout.write('\n'+name+'\t\t'); sys.stdout.flush()
            for cat in range(6):
                pos=np.where(obs_summary==cat)
                sys.stdout.write(str(np.nanmean(obs_summary[pos[0],pos[1],vari]))+'\t'); sys.stdout.flush()

        return self._obs_track_info

    # plotting
    def plot_on_map(self,ax,x_in,y_in,cat=None,latlon=False,**kwargs):
        if latlon:
            x,y=x_in,y_in
        if latlon==False:
            if isinstance(x_in,np.ndarray) or isinstance(x_in,list) or isinstance(x_in,da.core.dimarraycls.DimArray):
                x=self._lons[[int(yy) for yy in y_in],[int(xx) for xx in x_in]]
                y=self._lats[[int(yy) for yy in y_in],[int(xx) for xx in x_in]]
            elif isinstance(x_in,np.float64) or isinstance(x_in,int) or isinstance(x_in,float):
                x=self._lons[int(y_in),int(x_in)]
                y=self._lats[int(y_in),int(x_in)]
        if cat is not None:
            tmp=[]
            for i in range(len(x)-1):
                if np.isfinite(x[i+1]):
                    tmp.append(ax.plot(x[i:i+2],y[i:i+2],color=self._cat_colors[cat[i]],transform=self._transform,**kwargs))
            return tmp
        else:
            return ax.plot(x,y,transform=self._transform,**kwargs)

    def plot_surrounding(self,axes,time_steps=None):
        if time_steps is None:
            time_steps=self._time_i

        #plt.tight_layout()
        for t in time_steps:
            tmp,txt=[],[]
            ax=axes[0]; ax.set_title('mean sea level pressure')
            im=ax.pcolormesh(self._lons,self._lats,self._MSLP[t,:,:],transform=self._transform)
            im.set_cmap('bone'); ax.autoscale(False); ax.axis('off')

            ax=axes[1]; ax.set_title('temperature')
            im=ax.pcolormesh(self._lons,self._lats,self._T[t,1,:,:],transform=self._transform)
            im.set_cmap('bone'); ax.autoscale(False); ax.axis('off')

            ax=axes[2]; ax.set_title('10m wind speed')
            im=ax.pcolormesh(self._lons,self._lats,self._Wind10[t,:,:],vmin=0,vmax=15,transform=self._transform)
            im.set_cmap('bone'); ax.autoscale(False); ax.axis('off')

            for point in self._detected[self._detected[:,'t']==t].values.tolist():
                if point[3]==1:
                    tmp.append(self.plot_on_map(axes[0],int(point[2]),int(point[1]),c='b',marker='.'))
                    stats='wind: '+str(round(point[6],01))+'\nmslp: '+str(round(point[5],01))
                    txt.append(axes[3].text(self._lons[int(point[1]),int(point[2])],self._lats[int(point[1]),int(point[2])],stats,color='red',va='bottom',ha='right',fontsize=7))
                if point[4]==1:
                    tmp.append(self.plot_on_map(axes[1],int(point[2]),int(point[1]),c='g',marker='*'))

            ax=axes[3]; ax.set_title('10m wind [m/s] and mslp [mbar]')

            if self._obs_tc:
                obs_tc=np.where(abs(self._tc_time-self._yr_frac[t])<0.0004)
                if len(obs_tc[0])>0:
                    for oo in range(len(obs_tc[0])):
                        if np.isfinite(self._tc_sel['source_wind'].ix[obs_tc[0][oo],obs_tc[1][oo],0]):
                            tmp.append(axes[3].plot(self._tc_lon[obs_tc[0][oo],obs_tc[1][oo]],self._tc_lat[obs_tc[0][oo],obs_tc[1][oo]],color=self._cat_colors[self.tc_cat(self._tc_intens[obs_tc[0][oo],obs_tc[1][oo]],method='wind')],marker='.'))


            plt.suptitle(str(self._dates[t]))
            plt.savefig(self._working_dir+'track_surrounding/'+self._add_name+'_'+str(t)+'.png', bbox_inches = 'tight')

            # clean map
            for ax in axes:
                for imm in ax.images:
                    ax.images.remove(imm)
            for element in tmp:
                l = element.pop(0); wl = weakref.ref(l); l.remove(); del l
            for element in txt:
                element.remove()

    def plot_track_path(self,track):
        t=int(track.ix[np.nanargmin(track[:,'MSLP'].values),0])
        tmp,txt=[],[]

        if self._obs_tc:
            storms=np.where(abs(self._tc_time-self._yr_frac[t])<0.002)[0]
            for storm in set(storms):
                tmp+=self.plot_on_map(self._ax,self._tc_lon[storm,:],self._tc_lat[storm,:],cat=self.tc_cat(self._tc_intens[storm,:],method='wind'),latlon=True)
                last_pos=np.where(np.isfinite(self._tc_lon[storm,:]))[0][-1]
                txt.append(self._ax.text(self._tc_lon[storm,last_pos],self._tc_lat[storm,last_pos],''.join(self._tc_sel['name'].ix[storm,:])))

        tmp.append(self.plot_on_map(self._ax,track[:,'x'],track[:,'y'],c='orange'))
        tmp+=self.plot_on_map(self._ax,track[:,'x'],track[:,'y'],cat=self.tc_cat(track[:,'MSLP'].values),marker='.',linestyle='')
        self._ax.set_title(str(self._dates[t]))

        #tmp+=self.plot_on_map(self._m,self._detected[:,'x'],self._detected[:,'y'],marker='.',linestyle='',color='m')

        plt.savefig(self._working_dir+'track_path/'+str(self._identifier)+'_'+self._add_name+'_'+str(t)+'_'+str(self._id)+'.png')

        # clean map
        for element in tmp:
            l = element.pop(0); wl = weakref.ref(l); l.remove(); del l
        for element in txt:
            element.remove()

    def plot_season(self,out_name=None):
        tmp=[]
        if out_name is None:
            out_name=self._working_dir+'season_'+str(self._identifier)+'_found_tracks_'+self._add_name+'.png'

        self._ax.set_title('season '+self._identifier)#

        summary={0:[],1:[],2:[],3:[],4:[],5:[]}
        for id_,track in self._tcs.items():
            track=track[np.isfinite(track[:,'t']),:]
            tmp.append(self.plot_on_map(self._ax,track.ix[0,2],track.ix[0,1],linestyle='',marker='o',c='r'))
            tmp.append(self.plot_on_map(self._ax,track[:,'x'],track[:,'y'],linestyle='-',linewidth=0.5,c='r'))
            tmp+=self.plot_on_map(self._ax,track[:,'x'],track[:,'y'],cat=self.tc_cat(track[:,'MSLP'].values),marker='.',linestyle='')
            summary[max(self.tc_cat(track[:,'MSLP'].values))].append(id_)

        if self._obs_tc:
            for storm in range(len(self._tc_sel.storm)):
                tmp+=self.plot_on_map(self._ax,self._tc_lon[storm,:],self._tc_lat[storm,:],cat=self.tc_cat(self._tc_intens[storm,:],method='wind'),latlon=True)


        summary.pop(0)
        txt=[]
        for cat,y in zip(summary.keys(),[0.99,0.95,0.91,0.87,0.83]):
            txt.append(self._ax.text(0.005,y,self._cat_names[cat]+': '+''.join(['X']*len(summary[cat])),transform=self._ax.transAxes,color=self._cat_colors[cat],va='top',ha='left',fontsize=12))
        plt.tight_layout()
        plt.savefig(out_name)

        # clean map
        for element in txt:
            element.remove()
        for element in tmp:
            l = element.pop(0); wl = weakref.ref(l); l.remove(); del l

    def plot_detect_summary(self,thr_wind=17.5,out_name=None):
        tmp=[]
        if out_name is None:
            out_name=self._working_dir+'season_'+str(self._identifier)+'_found_positions_'+self._add_name+'.png'

        self._ax.set_title('season '+self._identifier)#

        detect=self._detected.copy()
        tmp.append(self.plot_on_map(self._ax,detect[:,'x'],detect[:,'y'],linestyle='',marker='o',c='g'))
        warm_core=detect[detect[:,'warm_core']==1]
        tmp.append(self.plot_on_map(self._ax,warm_core[:,'x'],warm_core[:,'y'],linestyle='',marker='v',c='r'))
        strong_wind=detect[detect[:,'Wind10']>=thr_wind]
        tmp.append(self.plot_on_map(self._ax,strong_wind[:,'x'],strong_wind[:,'y'],linestyle='',marker='^',c='y'))

        plt.tight_layout()
        plt.savefig(out_name)
        # clean map
        for element in tmp:
            l = element.pop(0); wl = weakref.ref(l); l.remove(); del l

    # analyze fields
    def get_box(self,y,x,window):
        y_min=int(max(0,y-window))
        y_max=int(min(self._lats.shape[0],y+window+1))
        x_min=int(max(0,x-window))
        x_max=int(min(self._lats.shape[1],x+window+1))
        return (y_min,y_max,x_min,x_max)

    def area_around(self,y,x,radius):
        box=self.get_box(y,x,radius)
        y_,x_=[],[]
        for i in range(box[0],box[1]):
            for j in range(box[2],box[3]):
                if ((y-i)**2+(x-j)**2)**0.5<=radius:
                    y_.append(i)
                    x_.append(j)
        return y_,x_

    def circle_around(self,y,x,radius):
        box=self.get_box(y,x,radius)
        y_,x_=[],[]
        for i in range(box[0],box[1]):
            for j in range(box[2],box[3]):
                if radius-1<=((y-i)**2+(x-j)**2)**0.5<=radius:
                    y_.append(i)
                    x_.append(j)
        return y_,x_

    def find_closed_contours(self,field,y,x,step=2,search_radius=30,n_contours=None,method='min'):
        '''
        finds closed contours around center
        center is supposed to be a minimum
        '''
        y_min=int(max(0,y-search_radius))
        y_max=int(min(field.shape[0],y+search_radius+1))
        x_min=int(max(0,x-search_radius))
        x_max=int(min(field.shape[1],x+search_radius+1))

        if method=='min':
            im=-field.copy()
            threshold=-field[y,x]
        else:
            im=field.copy()
            threshold=field[y,x]

        ny,nx=im.shape
        running=True
        ncont=0
        cont=np.zeros((ny, nx))
        while n_contours is None or ncont<n_contours:
            threshold-=step
            th, im_floodfill = cv2.threshold(im, threshold, 255, cv2.THRESH_BINARY_INV);
            mask = np.zeros((ny+2, nx+2), np.uint8)
            cv2.floodFill(im_floodfill, mask, (x,y), 1);
            y_,x_=np.where(mask[1:-1,1:-1]==1)
            if y_min in y_ or x_min in x_ or y_max-1 in y_ or x_max-1 in x_:
                cont[cont==0]=np.nan
                return cont,ncont
            cont=np.array(mask[1:-1,1:-1],np.float)
            ncont+=1

        cont[cont==0]=np.nan
        return cont,ncont

    # combine detected positions
    def combine_tracks(self,plot=True,search_radius=6,thr_wind=17.5,total_steps=12,warm_steps=8,strong_steps=0,consecutive_warm_strong_steps=6,overwrite=False):
        out_file=self._working_dir+'track_info_'+self._add_name+'.nc'
        if overwrite and os.path.isfile(out_file):
            os.system('rm '+out_file)
        elif overwrite==False and os.path.isfile(out_file):
            self._tcs=da.read_nc(out_file)
            return self._tcs

        def consecutive_sequence(zz):
            i,su,out=0,0,[]
            while i <len(zz):
                if zz[i]:
                    su+=1
                else:
                    out.append((i-su,su))
                    su=0
                i+=1
            out.append((i-su,su))
            return np.array(out)

        # convert distances from degrees into grid-cells
        search_radius=self.degree_to_step(search_radius)

        self._id=0
        self._tcs={}

        postions=self._detected.copy().values
        used_pos=[]
        for p in postions[postions[:,-1].argsort()[::-1],:].tolist():
            if p not in used_pos:
                track=[p]

                running=True
                #go backwards
                while True:
                    p=track[0]
                    if len(track)==1:
                        y_mo,x_mo=0,0
                    else:
                        y_mo,x_mo=p[1]-track[1][1],p[2]-track[1][2]
                    candidates=[]
                    for p_1 in postions[postions[:,0]==p[0]-1,:].tolist():
                        if ((p[1]+y_mo-p_1[1])**2+(p[2]+x_mo-p_1[2])**2)**0.5<search_radius:
                            candidates.append(p_1)
                            end=False
                    if len(candidates)>0:
                        track=[candidates[np.array(candidates)[:,-2].argmin()]]+track
                    else:
                        break

                #go forewards
                while True:
                    p=track[-1]
                    if len(track)==1:
                        y_mo,x_mo=0,0
                    else:
                        y_mo,x_mo=p[1]-track[-2][1],p[2]-track[-2][2]
                    candidates=[]
                    for p_1 in postions[postions[:,0]==p[0]+1,:].tolist():
                        if ((p[1]+y_mo-p_1[1])**2+(p[2]+x_mo-p_1[2])**2)**0.5<search_radius:
                            candidates.append(p_1)
                            end=False
                    if len(candidates)>0:
                        track=track+[candidates[np.array(candidates)[:,-2].argmin()]]
                    else:
                        break

                if sum([pp in used_pos for pp in track])/float(len(track))<0.3:
                    used_pos+=track

                    track=da.DimArray(track,axes=[np.array(track)[:,0],['t','y','x','pressure_low','warm_core','MSLP','Wind10']],dims=['time','z'])
                    save_track=True
                    start_pos=track.values[0,1:3]
                    if track.shape[0]<total_steps:
                        save_track=False
                    if track[track[:,'warm_core']==1].shape[0]<warm_steps:
                        save_track=False
                    else:
                        start_pos=track[track[:,'warm_core']==1].values[0,1:3]
                    if track[track[:,'Wind10']>=thr_wind].shape[0]<strong_steps:
                        save_track=False
                    else:
                        start_pos=track[track[:,'Wind10']>=thr_wind].values[0,1:3]
                    if consecutive_warm_strong_steps>0:
                        warm_strong=track[(track[:,'Wind10']>=thr_wind) & (track[:,'warm_core']==1)]
                        consecutive=np.diff(warm_strong[:,'t'],1)==1
                        consec_info=consecutive_sequence(consecutive)
                        if max(consec_info[:,1])<consecutive_warm_strong_steps:
                            save_track=False
                        else:
                            first_of_consec=consec_info[np.argmax(consec_info[:,1]),0]
                            start_pos=warm_strong.ix[first_of_consec,1:3]

                    if self._lats[int(start_pos[0]),int(start_pos[1])]>=40:
                        save_track=False

                    if save_track:
                        self._tcs[self._identifier+'_'+str(self._id)]=track
                        if plot:    self.plot_track_path(track)
                        self._id+=1

        self._tcs=da.Dataset(self._tcs)
        self._tcs.write_nc(out_file,mode='w')
        return self._tcs

    # detect positions
    def detect_contours(self,overwrite=False,p_radius=27,warm_core_size=3,cores_distance=1,neighborhood_size=3):
        self._add_name='contours'
        out_file=self._working_dir+'detected_positions_'+self._add_name+'.nc'
        if overwrite and os.path.isfile(out_file):
            os.system('rm '+out_file)
        elif overwrite==False and os.path.isfile(out_file):
            self._detected=da.read_nc(out_file)['detected']
            return self._detected

        # convert distances from degrees into grid-cells
        p_radius=self.degree_to_step(p_radius)
        neighborhood_size=self.degree_to_step(neighborhood_size)
        warm_core_size=self.degree_to_step(warm_core_size)**2
        cores_distance=self.degree_to_step(cores_distance)

        detect=np.array([[np.nan]*7])
        print('detecting\n10------50-------100')
        for t,progress in zip(self._time_i,np.array([['-']+['']*(len(self._time_i)/20+1)]*20).flatten()[0:len(self._time_i)]):
            sys.stdout.write(progress); sys.stdout.flush()
            coords=peak_local_max(-self._MSLP_smoothed[t,:,:], min_distance=int(neighborhood_size))
            for y_p,x_p in zip(coords[:,0],coords[:,1]):
                tc_area,ncont=self.find_closed_contours(self._MSLP_smoothed[t,:,:],y_p,x_p,step=1,search_radius=p_radius,method='min')
                if ncont>0:
                    tmp=[t,y_p,x_p,1,0,0,0]
                    # have to check boundary issues here
                    box=self.get_box(y_p,x_p,cores_distance)
                    y_,x_=np.where(self._T[t,1,box[0]:box[1],box[2]:box[3]]==self._T[t,1,box[0]:box[1],box[2]:box[3]].max())
                    y_t,x_t=box[0]+y_[0],box[2]+x_[0]
                    warm_core_area,ncont=self.find_closed_contours(self._T[t,1,:,:],y_t,x_t,step=1,search_radius=p_radius,n_contours=1,method='max')
                    yy,xx=np.where(warm_core_area==1)
                    if len(np.where(warm_core_area==1)[0])<warm_core_size and ncont==1:
                        tmp[4]=1

                    tmp[5]=self._MSLP[t,y_p,x_p]
                    tmp[6]=np.nanmax(tc_area*self._Wind10[t,:,:])
                    if np.isnan(tmp[6]):
                        print(ncont)
                    detect=np.concatenate((detect,np.array([tmp])))

        self._detected=da.DimArray(np.array(detect[1:,:]),axes=[range(detect.shape[0]-1),['t','y','x','pressure_low','warm_core','MSLP','Wind10']],dims=['ID','z'])
        da.Dataset({'detected':self._detected}).write_nc(out_file,mode='w')
        print('done')
        return self._detected

    def detect_knutson2007(self,thr_vort=3.5*10**(-5),dis_vort_max=4,dis_cores=2,thr_MSLP_inc=4,dis_MSLP_inc=5,thr_T_drop=0.8,dis_T_drop=5,tc_size=7,overwrite=False):
        self._add_name='knutson2007'
        out_file=self._working_dir+'detected_positions_thresholds_'+self._add_name+'.nc'
        if overwrite and os.path.isfile(out_file):
            os.system('rm '+out_file)
        elif overwrite==False and os.path.isfile(out_file):
            self._detected=da.read_nc(out_file)['detected']
            return self._detected

        '''
        Required Thresholds:
        thr_vort:       float [1/s]:    threshold for relative vorticity maxima
        dis_vort_max:   float [deg]:    minimal distance between vorticity maxima
        dis_cores:      float [deg]:    maximal distances between vort max and mslp min (mslp min and warm core)
        thr_MSLP_inc:   float [hPa]:    increase in MSLP from center over dis_MSLP_inc
        dis_MSLP_inc:   float [deg]:    distance over which MSLP should increase by thr_MSLP_inc
        thr_T_drop:     float [K]:      temperature drop from warm core center over dis_T_decrease
        dis_T_drop:     float [deg]:    distance over which T should decrease by thr_T_decrease
        tc_size:        float [deg]:    radius within which maximal Wind10 is searched for
        '''

        # convert distances from degrees into grid-cells
        for distance in [dis_vort_max,dis_cores,dis_MSLP_inc,dis_T_drop,tc_size]:
            distance=self.degree_to_step(distance)

        detect=np.array([[np.nan]*7])
        print('detecting\n10------50-------100')
        for t,progress in zip(self._time_i,np.array([['-']+['']*(len(self._time_i)/20+1)]*20).flatten()[0:len(self._time_i)]):
            sys.stdout.write(progress); sys.stdout.flush()
            # i vort max
            vo_=self._VO[t,:,:]
            vo_[vo_<thr_vort]=0
            coords = peak_local_max(vo_, min_distance=int(dis_vort_max))
            if coords.shape[0]>0:
                for y_v,x_v in zip(coords[:,0],coords[:,1]):
                    y_circ,x_circ=self.area_around(y_v,x_v,dis_cores)
                    p_window=self._MSLP[t,y_circ,x_circ].flatten()
                    i=np.where(p_window==p_window.min())[0][0]; y_p,x_p=y_circ[i],x_circ[i]
                    y_circ,x_circ=self.circle_around(y_p,x_p,dis_MSLP_inc)
                    # ii relative pressure min
                    # could also be self._MSLP[t,y_circ,x_circ].mean()
                    if self._MSLP[t,y_circ,x_circ].min()-self._MSLP[t,y_p,x_p]>thr_MSLP_inc:
                        tmp=[t,y_p,x_p,1,0,0,0]
                        y_circ,x_circ=self.area_around(y_p,x_p,dis_cores)
                        # iv warm core
                        t_window=self._T[t,y_circ,x_circ].flatten()
                        i=np.where(t_window==t_window.max())[0][0]; y_t,x_t=y_circ[i],x_circ[i]
                        y_circ,x_circ=self.circle_around(y_t,x_t,dis_T_drop)
                        if self._T[t,y_t,x_t]-self._T[t,y_circ,x_circ].max()>thr_T_drop:
                            tmp[4]=1
                        # iii wind speed
                        tmp[5]=self._MSLP[t,y_p,x_p]
                        y_circ,x_circ=self.circle_around(y_p,x_p,tc_size)
                        tmp[6]=self._Wind10[t,y_circ,x_circ].max()
                        detect=np.concatenate((detect,np.array([tmp])))

        self._detected=da.DimArray(np.array(detect[1:,:]),axes=[range(detect.shape[0]-1),['t','y','x','pressure_low','warm_core','MSLP','Wind10']],dims=['ID','z'])
        da.Dataset({'detected':self._detected}).write_nc(out_file,mode='w')
        print('done')
        return self._detected

    def detect_thresholds_simple(self,thr_vort=3.5*10**(-5),dis_vort_max=4,dis_cores=2,thr_MSLP_inc=2,dis_MSLP_inc=5,thr_T_drop=1,dis_T_drop=3,tc_size=5,overwrite=False):
        self._add_name='thresholds'
        out_file=self._working_dir+'detected_positions_thresholds_'+self._add_name+'.nc'
        if overwrite and os.path.isfile(out_file):
            os.system('rm '+out_file)
        elif overwrite==False and os.path.isfile(out_file):
            self._detected=da.read_nc(out_file)['detected']
            return self._detected

        '''
        Required Thresholds:
        thr_vort:       float [1/s]:    threshold for relative vorticity maxima
        dis_vort_max:   float [deg]:    minimal distance between vorticity maxima
        dis_cores:      float [deg]:    maximal distances between vort max and mslp min (mslp min and warm core)
        dis_MSLP_inc:   float [deg]:    distance over which MSLP should increase by thr_MSLP_inc
        thr_T_drop:     float [K]:      temperature drop from warm core center over dis_T_decrease
        dis_T_drop:     float [deg]:    distance over which T should decrease by thr_T_decrease
        tc_size:        float [deg]:    radius within which maximal Wind10 is searched for
        '''

        # convert distances from degrees into grid-cells
        for distance in [dis_vort_max,dis_cores,dis_MSLP_inc,dis_T_drop,tc_size]:
            distance=self.degree_to_step(distance)

        detect=np.array([[np.nan]*7])
        print('detecting\n10------50-------100')
        for t,progress in zip(self._time_i,np.array([['-']+['']*(len(self._time_i)/20+1)]*20).flatten()[0:len(self._time_i)]):
            sys.stdout.write(progress); sys.stdout.flush()
            # i vort max
            vo_=self._VO[t,:,:]
            vo_[vo_<thr_vort]=0
            coords = peak_local_max(vo_, min_distance=int(dis_vort_max))
            if coords.shape[0]>0:
                for y_v,x_v in zip(coords[:,0],coords[:,1]):
                    y_circ,x_circ=self.area_around(y_v,x_v,dis_cores)
                    p_window=self._MSLP[t,y_circ,x_circ].flatten()
                    i=np.where(p_window==p_window.min())[0][0]; y_p,x_p=y_circ[i],x_circ[i]
                    y_area,x_area=self.area_around(y_p,x_p,dis_MSLP_inc)
                    # ii relative pressure min
                    if self._MSLP[t,y_p,x_p]==self._MSLP[t,y_area,x_area].min():
                        box_1=self.get_box(y_p,x_p,cores_distance)
                        box_2=self.get_box(y_p,x_p,tc_size)
                        tmp=[t,y_p,x_p,1,0,0,0]
                        # iv warm core
                        if self._T is None:
                            tmp[4]=1
                        elif self._T[t,1,box_1[0]:box_1[1],box_1[2]:box_1[3]].max()-self._T[t,1,box_2[0]:box_2[1],box_2[2]:box_2[3]].mean()>thr_T_drop:
                            tmp[4]=1
                        # iii wind speed
                        tmp[5]=self._MSLP[t,y_p,x_p]
                        tmp[6]=self._Wind10[t,box_2[0]:box_2[1],box_2[2]:box_2[3]].max()
                        detect=np.concatenate((detect,np.array([tmp])))

        self._detected=da.DimArray(np.array(detect[1:,:]),axes=[range(detect.shape[0]-1),['t','y','x','pressure_low','warm_core','MSLP','Wind10']],dims=['ID','z'])
        da.Dataset({'detected':self._detected}).write_nc(out_file,mode='w')
        print('done')
        return self._detected
